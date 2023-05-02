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
# version ='0.1'
# ---------------------------------------------------------------------------
"""Modules for raw projection data normalization"""

from typing import Tuple
import cupy as cp
import numpy as np
import nvtx
from cupy import uint16, float32, mean
from httomolib.decorator import method_proj

__all__ = ["normalize"]


def _normalize_max_slices(
    non_slice_dims_shape: Tuple[int, int],
    output_dims: Tuple[int, int],
    dtype: cp.dtype, available_memory: int, **kwargs
) -> Tuple[int, np.dtype]:
    """Calculate the max chunk size it can fit in the available memory"""

    # normalize needs space to store the darks + flats and their means as a fixed cost
    flats_mean_space = np.prod(non_slice_dims_shape) * float32().nbytes
    darks_mean_space = np.prod(non_slice_dims_shape) * float32().nbytes
    available_memory -= flats_mean_space + darks_mean_space

    # it also needs space for data input and output (we don't care about slice_dim)
    # data: [x, 10, 20], dtype => other_dims = [10, 20]
    in_slice_memory = np.prod(non_slice_dims_shape) * uint16().nbytes
    out_slice_memory = np.prod(output_dims) * float32().nbytes
    slice_memory = in_slice_memory + out_slice_memory
    max_slices = available_memory // slice_memory  # rounds down

    return max_slices, float32()


@method_proj(calc_max_slices=_normalize_max_slices)
@nvtx.annotate()
def normalize(
    data: cp.ndarray,
    flats: cp.ndarray,
    darks: cp.ndarray,
    cutoff: float = 10.0,
    minus_log: bool = False,
    nonnegativity: bool = False,
    remove_nans: bool = False,
) -> cp.ndarray:
    """
    Normalize raw projection data using the flat and dark field projections.
    This is a raw CUDA kernel implementation with CuPy wrappers.

    Parameters
    ----------
    data : cp.ndarray
        Projection data as a CuPy array.
    flats : cp.ndarray
        3D flat field data as a CuPy array.
    darks : cp.ndarray
        3D dark field data as a CuPy array.
    cutoff : float, optional
        Permitted maximum value for the normalised data.
    minus_log : bool, optional
        Apply negative log to the normalised data.
    nonnegativity : bool, optional
        Remove negative values in the normalised data.
    remove_nans : bool, optional
        Remove NaN values in the normalised data.

    Returns
    -------
    cp.ndarray
        Normalised 3D tomographic data as a CuPy array.
    """
    _check_valid_input(data, flats, darks)

    dark0 = cp.empty(darks.shape[1:], dtype=float32)
    flat0 = cp.empty(flats.shape[1:], dtype=float32)
    out = cp.empty(data.shape, dtype=float32)
    mean(darks, axis=0, dtype=float32, out=dark0)
    mean(flats, axis=0, dtype=float32, out=flat0)

    kernel_name = "normalisation"
    kernel = r"""
        float denom = float(flats) - float(darks);
        if (denom < eps) {
            denom = eps;
        }
        float v = (float(data) - float(darks))/denom;
        if (v > cutoff) {
            v = cutoff;
        }
        """
    if minus_log:
        kernel += "v = -log(v);\n"
        kernel_name += "_mlog"
    if nonnegativity:
        kernel += "if (v < 0.0f) v = 0.0f;\n"
        kernel_name += "_nneg"
    if remove_nans:
        kernel += "if (isnan(v)) v = 0.0f;\n"
        kernel_name += "_remnan"
    kernel += "out = v;\n"

    normalisation_kernel = cp.ElementwiseKernel(
        "T data, U flats, V darks, raw float32 cutoff",
        "float32 out",
        kernel,
        kernel_name,
        options=("-std=c++11",),
        loop_prep="constexpr float eps = 1.0e-07;",
        no_return=True,
    )

    normalisation_kernel(data, flat0, dark0, float32(cutoff), out)

    return out


def _check_valid_input(data, flats, darks) -> None:
    """Helper function to check the validity of inputs to normalisation functions"""
    if data.ndim != 3:
        raise ValueError("Input data must be a 3D stack of projections")

    if flats.ndim not in (2, 3):
        raise ValueError("Input flats must be 2D or 3D data only")

    if darks.ndim not in (2, 3):
        raise ValueError("Input darks must be 2D or 3D data only")

    if flats.ndim == 2:
        flats = flats[cp.newaxis, :, :]
    if darks.ndim == 2:
        darks = darks[cp.newaxis, :, :]
