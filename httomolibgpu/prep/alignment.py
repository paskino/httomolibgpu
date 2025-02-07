#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# Copyright 2022 Diamond Light Source Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ---------------------------------------------------------------------------
# Created By  : Tomography Team at DLS <scientificsoftware@diamond.ac.uk>
# Created Date: 01 November 2022
# ---------------------------------------------------------------------------
"""Modules for data correction"""

import os
from typing import Dict, List, Optional, Tuple

import cupy as cp
from cupyx.scipy.ndimage import map_coordinates
import nvtx
import numpy as np


from httomolibgpu.decorator import method_proj

__all__ = [
    "distortion_correction_proj",
]


def _calc_max_slices_distortion_correction_proj(
    non_slice_dims_shape: Tuple[int, int],
    dtype: np.dtype,
    available_memory: int, **kwargs
) -> Tuple[int, np.dtype, Tuple[int, int]]:
    # calculating memory is a bit more involved as various small temporary arrays
    # are used. We revert to a rough estimation using only the larger elements and
    # a safety margin
    height, width = non_slice_dims_shape[0], non_slice_dims_shape[1]
    lists_size = (width + height) * np.float64().nbytes
    meshgrid_size = (width * height * 2) * np.float64().nbytes
    ru_mat_size = meshgrid_size // 2
    fact_mat_size = ru_mat_size
    xd_mat_size = yd_mat_size = fact_mat_size // 2   # float32
    indices_size = xd_mat_size + yd_mat_size

    slice_size = np.prod(non_slice_dims_shape) * dtype.itemsize
    processing_size = slice_size * 4  # temporaries in final for loop

    available_memory -= (
        lists_size
        + meshgrid_size
        + ru_mat_size
        + fact_mat_size * 2
        + xd_mat_size
        + yd_mat_size
        + processing_size
        + indices_size * 3  # The x 3 here is for additional safety margin 
    )                       # to allow for memory for temporaries

    return (int(available_memory // slice_size), dtype, non_slice_dims_shape)


## %%%%%%%%%%%%%%%%%%%%%%%%%distortion_correction_proj%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%  ##
# CuPy implementation of distortion correction from Savu
# TODO: Needs to be implementation from TomoPy
@method_proj(_calc_max_slices_distortion_correction_proj)
@nvtx.annotate()
def distortion_correction_proj(
    data: cp.ndarray,
    metadata_path: str,
    preview: Dict[str, List[int]],
    center_from_left: Optional[float] = None,
    center_from_top: Optional[float] = None,
    polynomial_coeffs: Optional[List[float]] = None,
    crop: int = 0,
):
    """Correct the radial distortion in the given stack of 2D images.

    Parameters
    ----------
    data : cp.ndarray
        The stack of 2D images to apply the distortion correction to.

    metadata_path : str
        The path to the file containing the distortion coefficients for the
        data.

    preview : Dict[str, List[int]]
        A dict containing three key-value pairs:
        - a list containing the `start` value of each dimension
        - a list containing the `stop` value of each dimension
        - a list containing the `step` value of each dimension

    center_from_left : optional, float
        If no metadata file with the distortion coefficients is provided, then
        the x center can be provided as a parameter by passing the horizontal
        distortion center as measured from the left of the image.

    center_from_top : optional, float
        If no metadata file with the distortion coefficients is provided, then
        the y center can be provided as a parameter by passing the horizontal
        distortion center as measured from the top of the image.

    polynomial_coeffs : optional, List[float]
        If no metadata file with the distortion coefficients is provided, then
        the polynomial coefficients for the distortion correction can be
        provided as a parameter by passing a list of floats.

    crop : optional, int
        The amount to crop both the height and width of the stack of images
        being corrected.

    Returns
    -------
    cp.ndarray
        The corrected stack of 2D images.
    """
    # Check if it's a stack of 2D images, or only a single 2D image
    if len(data.shape) == 2:
        data = cp.expand_dims(data, axis=0)

    # Get info from metadat txt file
    x_center, y_center, list_fact = _load_metadata_txt(metadata_path)

    shift = preview["starts"]
    step = preview["steps"]
    x_dim = 1
    y_dim = 0
    step_check = max([step[i] for i in [x_dim, y_dim]]) > 1
    if step_check:
        msg = (
            "\n***********************************************\n"
            "!!! ERROR !!! -> Method doesn't work with the step in"
            " the preview larger than 1 \n"
            "***********************************************\n"
        )
        raise ValueError(msg)

    x_offset = shift[x_dim]
    y_offset = shift[y_dim]
    msg = ""
    x_center = 0.0
    y_center = 0.0
    if metadata_path is None:
        x_center = cp.asarray(center_from_left, dtype=cp.float32) - x_offset
        y_center = cp.asarray(center_from_top, dtype=cp.float32) - y_offset
        list_fact = cp.float32(tuple(polynomial_coeffs))
    else:
        if not os.path.isfile(metadata_path):
            msg = "!!! No such file: %s !!!" " Please check the file path" % str(
                metadata_path
            )
            raise ValueError(msg)
        try:
            (x_center, y_center, list_fact) = _load_metadata_txt(metadata_path)
            x_center = x_center - x_offset
            y_center = y_center - y_offset
        except IOError as exc:
            msg = (
                "\n*****************************************\n"
                "!!! ERROR !!! -> Can't open this file: %s \n"
                "*****************************************\n\
                  "
                % str(metadata_path)
            )
            raise ValueError(msg) from exc

    height, width = data.shape[y_dim + 1], data.shape[x_dim + 1]
    xu_list = cp.arange(width) - x_center
    yu_list = cp.arange(height) - y_center
    xu_mat, yu_mat = cp.meshgrid(xu_list, yu_list)
    ru_mat = cp.sqrt(xu_mat**2 + yu_mat**2)
    fact_mat = cp.sum(
        cp.asarray([factor * ru_mat**i for i, factor in enumerate(list_fact)]), axis=0
    )
    xd_mat = cp.asarray(
        cp.clip(x_center + fact_mat * xu_mat, 0, width - 1), dtype=cp.float32
    )
    yd_mat = cp.asarray(
        cp.clip(y_center + fact_mat * yu_mat, 0, height - 1), dtype=cp.float32
    )

    diff_y = cp.max(yd_mat) - cp.min(yd_mat)
    if diff_y < 1:
        msg = (
            "\n*****************************************\n\n"
            "!!! ERROR !!! -> You need to increase the preview"
            " size for this plugin to work \n\n"
            "*****************************************\n"
        )
        raise ValueError(msg)

    indices = [cp.reshape(yd_mat, (-1, 1)), cp.reshape(xd_mat, (-1, 1))]
    indices = cp.asarray(indices, dtype=cp.float32)

    # Loop over images and unwarp them
    for i in range(data.shape[0]):
        mat_corrected = cp.reshape(
            map_coordinates(data[i], indices, order=1, mode="reflect"), (height, width)
        )
        data[i] = mat_corrected[crop : height - crop, crop : width - crop]
    return data


# CuPy implementation of distortion correction from Discorpy
# https://github.com/DiamondLightSource/discorpy/blob/67743842b60bf5dd45b21b8460e369d4a5e94d67/discorpy/post/postprocessing.py#L111-L148
# (which is the same as the TomoPy version
# https://github.com/tomopy/tomopy/blob/c236a2969074f5fc70189fb5545f0a165924f916/source/tomopy/prep/alignment.py#L950-L981
# but with the additional params `order` and `mode`).
@method_proj(_calc_max_slices_distortion_correction_proj)
@nvtx.annotate()
def distortion_correction_proj_discorpy(
    data: cp.ndarray,
    metadata_path: str,
    preview: Dict[str, List[int]],
    order: int = 1,
    mode: str = "reflect",
):
    """Unwarp a stack of images using a backward model.

    Parameters
    ----------
    data : cp.ndarray
        3D array.

    metadata_path : str
        The path to the file containing the distortion coefficients for the
        data.

    preview : Dict[str, List[int]]
        A dict containing three key-value pairs:
        - a list containing the `start` value of each dimension
        - a list containing the `stop` value of each dimension
        - a list containing the `step` value of each dimension

    order : int, optional.
        The order of the spline interpolation.

    mode : {'reflect', 'grid-mirror', 'constant', 'grid-constant', 'nearest',
           'mirror', 'grid-wrap', 'wrap'}, optional
        To determine how to handle image boundaries.

    Returns
    -------
    cp.ndarray
        3D array. Distortion-corrected image(s).
    """
    # Check if it's a stack of 2D images, or only a single 2D image
    if len(data.shape) == 2:
        data = cp.expand_dims(data, axis=0)

    # Get info from metadata txt file
    xcenter, ycenter, list_fact = _load_metadata_txt(metadata_path)

    # Use preview information to offset the x and y coords of the center of
    # distortion
    shift = preview["starts"]
    step = preview["steps"]
    x_dim = 1
    y_dim = 0
    step_check = max([step[i] for i in [x_dim, y_dim]]) > 1
    if step_check:
        msg = (
            "\n***********************************************\n"
            "!!! ERROR !!! -> Method doesn't work with the step in"
            " the preview larger than 1 \n"
            "***********************************************\n"
        )
        raise ValueError(msg)

    x_offset = shift[x_dim]
    y_offset = shift[y_dim]
    xcenter = xcenter - x_offset
    ycenter = ycenter - y_offset

    height, width = data.shape[y_dim + 1], data.shape[x_dim + 1]
    xu_list = cp.arange(width) - xcenter
    yu_list = cp.arange(height) - ycenter
    xu_mat, yu_mat = cp.meshgrid(xu_list, yu_list)
    ru_mat = cp.sqrt(xu_mat**2 + yu_mat**2)
    fact_mat = cp.sum(
        cp.asarray([factor * ru_mat**i for i, factor in enumerate(list_fact)]), axis=0
    )
    xd_mat = cp.asarray(
        cp.clip(xcenter + fact_mat * xu_mat, 0, width - 1), dtype=cp.float32
    )
    yd_mat = cp.asarray(
        cp.clip(ycenter + fact_mat * yu_mat, 0, height - 1), dtype=cp.float32
    )
    indices = [cp.reshape(yd_mat, (-1, 1)), cp.reshape(xd_mat, (-1, 1))]
    indices = cp.asarray(indices, dtype=cp.float32)

    # Loop over images and unwarp them
    for i in range(data.shape[0]):
        mat = map_coordinates(data[i], indices, order=order, mode=mode)
        mat = cp.reshape(mat, (height, width))
        data[i] = mat

    return data


def _load_metadata_txt(file_path):
    """
    Load distortion coefficients from a text file.
    Order of the infor in the text file:
    xcenter
    ycenter
    factor_0
    factor_1
    factor_2
    ...
    Parameters
    ----------
    file_path : str
        Path to the file
    Returns
    -------
    tuple of float and list of floats
        Tuple of (xcenter, ycenter, list_fact).
    """
    with open(file_path, "r") as f:
        x = f.read().splitlines()
        list_data = []
        for i in x:
            list_data.append(float(i.split()[-1]))
    xcenter = list_data[0]
    ycenter = list_data[1]
    list_fact = list_data[2:]

    return xcenter, ycenter, list_fact


## %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%  ##
