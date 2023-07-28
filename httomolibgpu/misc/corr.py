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
# Created Date: 21/October/2022
# ---------------------------------------------------------------------------
""" Module for data correction """

try:
    import cupy as cp
except ImportError:
    print("Cupy library is a required dependency for HTTomolibgpu, please install")
from typing import Tuple
import numpy as np
import nvtx

from httomolibgpu.cuda_kernels import load_cuda_module
from httomolibgpu.decorator import calc_max_slices_default, method_all

__all__ = [
    "median_filter3d",
    "remove_outlier3d",
]


@method_all(calc_max_slices=calc_max_slices_default)
@nvtx.annotate()
def median_filter3d(
    data: cp.ndarray, kernel_size: int = 3, dif: float = 0.0
) -> cp.ndarray:
    """
    Apply 3D median or dezinger (when dif>0) filter to a 3D array.

    Parameters
    ----------
    data : cp.ndarray
        Input CuPy 3D array either float32 or uint16 data type.
    kernel_size : int, optional
        The size of the filter's kernel.
    dif : float, optional
        Expected difference value between outlier value and the
        median value of the array, leave equal to 0 for classical median.

    Returns
    -------
    ndarray
        Median filtered 3D CuPy array either float32 or uint16 data type.

    Raises
    ------
    ValueError
        If the input array is not three dimensional.
    """
    input_type = data.dtype

    if input_type not in ["float32", "uint16"]:
        raise ValueError("The input data should be either float32 or uint16 data type")

    if data.ndim == 3:
        if 0 in data.shape:
            raise ValueError("The length of one of dimensions is equal to zero")
    else:
        raise ValueError("The input array must be a 3D array")

    if kernel_size not in [3, 5, 7, 9, 11, 13]:
        raise ValueError("Please select a correct kernel size: 3, 5, 7, 9, 11, 13")

    kernel_args = "median_general_kernel<{0}, {1}>".format(
        "float" if input_type == "float32" else "unsigned short", kernel_size
    )
    median_module = load_cuda_module("median_kernel", name_expressions=[kernel_args])
    median3d = median_module.get_function(kernel_args)

    out = cp.empty(data.shape, dtype=input_type, order="C")

    dz, dy, dx = data.shape
    # setting grid/block parameters
    block_x = 128
    block_dims = (block_x, 1, 1)
    grid_x = (dx + block_x - 1) // block_x
    grid_y = dy
    grid_z = dz
    grid_dims = (grid_x, grid_y, grid_z)

    params = (data, out, dif, dz, dy, dx)

    median3d(grid_dims, block_dims, params)

    return out


@method_all(calc_max_slices=calc_max_slices_default)
def remove_outlier3d(
    data: cp.ndarray, kernel_size: int = 3, dif: float = 0.1
) -> cp.ndarray:
    """
    Selectively applies 3D median filter to a 3D array to remove outliers. Also called a dezinger.

    Parameters
    ----------
    data : cp.ndarray
        Input CuPy 3D array either float32 or uint16 data type.
    kernel_size : int, optional
        The size of the filter's kernel.
    dif : float, optional
        Expected difference value between outlier value and the
        median value of the array.

    Returns
    -------
    ndarray
        Dezingered filtered 3D CuPy array either float32 or uint16 data type.

    Raises
    ------
    ValueError
        If the input array is not three dimensional.
    """
    return median_filter3d(data=data, kernel_size=kernel_size, dif=dif)