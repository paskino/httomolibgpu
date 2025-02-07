import time

import cupy as cp
import numpy as np
import pytest
from cupy.cuda import nvtx
from httomolibgpu.prep.phase import fresnel_filter, paganin_filter_savu, paganin_filter_tomopy
from numpy.testing import assert_allclose
from httomolibgpu import method_registry
from tests import MaxMemoryHook

eps = 1e-6


@cp.testing.gpu
def test_fresnel_filter_projection(data):
    # --- testing the Fresnel filter on tomo_standard ---#
    pattern = "PROJECTION"
    ratio = 100.0
    filtered_data = fresnel_filter(data, pattern, ratio).get()

    assert_allclose(np.mean(filtered_data), 802.1125, rtol=eps)
    assert_allclose(np.max(filtered_data), 1039.5293)
    assert_allclose(np.min(filtered_data), 95.74562)

    #: make sure the output is float32
    assert filtered_data.dtype == np.float32
    assert fresnel_filter.meta.pattern == 'projection'
    assert 'fresnel_filter' in method_registry['httomolibgpu']['prep']['phase']


@cp.testing.gpu
def test_fresnel_filter_sinogram(data):
    pattern = "SINOGRAM"
    ratio = 100.0
    filtered_data = fresnel_filter(data, pattern, ratio).get()

    assert_allclose(np.mean(filtered_data), 806.74347, rtol=eps)
    assert_allclose(np.max(filtered_data), 1063.7007)
    assert_allclose(np.min(filtered_data), 87.91508)

    #: make sure the output is float32
    assert filtered_data.dtype == np.float32


@cp.testing.gpu
def test_fresnel_filter_1D_raises(ensure_clean_memory):
    _data = cp.ones(10)
    with pytest.raises(ValueError):
        fresnel_filter(_data, "SINOGRAM", 100.0)

    _data = None  #: free up GPU memory


@cp.testing.gpu
def test_paganin_filter(data):
    # --- testing the Paganin filter on tomo_standard ---#
    filtered_data = paganin_filter_savu(data).get()
    
    assert filtered_data.ndim == 3
    assert_allclose(np.mean(filtered_data), -770.5339, rtol=eps)
    assert_allclose(np.max(filtered_data), -679.80945, rtol=eps)

    #: make sure the output is float32
    assert filtered_data.dtype == np.float32


@cp.testing.gpu
@pytest.mark.parametrize("pad", [0, 31, 100])
@pytest.mark.parametrize("slices", [15, 51, 160])
@pytest.mark.parametrize("dtype", [np.uint16, np.float32])
def test_paganin_filter_meta(pad, slices, dtype, ensure_clean_memory):    
    # --- testing the Paganin filter on tomo_standard ---#
    cache = cp.fft.config.get_plan_cache()
    cache.clear()
    kwargs = dict(pad_x=pad, pad_y=pad)
    data = cp.random.random_sample((slices, 111, 121), dtype=np.float32)
    if dtype == np.uint16:
        data = cp.asarray(data * 300.0, dtype=np.uint16)    
    hook = MaxMemoryHook(data.size * data.itemsize)
    with hook:
        paganin_filter_savu(data, **kwargs)
    
    # make sure estimator function is within range (80% min, 100% max)
    max_mem = hook.max_mem
    actual_slices = data.shape[0]
    estimated_slices, dtype_out, output_dims = paganin_filter_savu.meta.calc_max_slices(
        0, 
        (data.shape[1], data.shape[2]),
        data.dtype, max_mem, **kwargs)
    assert estimated_slices <= actual_slices
    assert estimated_slices / actual_slices >= 0.8    

    assert paganin_filter_savu.meta.pattern == 'projection'
    assert 'paganin_filter_savu' in method_registry['httomolibgpu']['prep']['phase']


@cp.testing.gpu
def test_paganin_filter_energy100(data):
    filtered_data = paganin_filter_savu(data, energy=100.0).get()

    assert_allclose(np.mean(filtered_data), -778.61926, rtol=1e-05)
    assert_allclose(np.min(filtered_data), -808.9013, rtol=eps)

    assert filtered_data.ndim == 3
    assert filtered_data.dtype == np.float32


@cp.testing.gpu
def test_paganin_filter_padmean(data):
    filtered_data = paganin_filter_savu(data, pad_method="mean").get()

    assert_allclose(np.mean(filtered_data), -765.3401, rtol=eps)
    assert_allclose(np.min(filtered_data), -793.68787, rtol=eps)
    # test a few other slices to ensure shifting etc is right
    assert_allclose(
        filtered_data[0, 50, 1:5],
        [-785.60736, -786.20215, -786.7521, -787.25494],
        rtol=eps,
    )
    assert_allclose(
        filtered_data[0, 50, 40:42], [-776.6436, -775.1906], rtol=eps, atol=1e-5
    )
    assert_allclose(
        filtered_data[0, 60:63, 90],
        [-737.75104, -736.6097, -735.49884],
        rtol=eps,
        atol=1e-5,
    )


@cp.testing.gpu
@pytest.mark.perf
def test_paganin_filter_performance(ensure_clean_memory):
    # Note: low/high and size values taken from sample2_medium.yaml real run

    # this test needs ~20GB of memory with 1801 - we'll divide depending on GPU memory
    dev = cp.cuda.Device()
    mem_80percent = 0.8 * dev.mem_info[0]
    size = 1801
    required_mem = 20 * 1024 * 1024 * 1024
    if mem_80percent < required_mem:
        size = int(np.ceil(size / required_mem * mem_80percent))
        print(f"Using smaller size of ({size}, 5, 2560) due to memory restrictions")

    data_host = np.random.random_sample(size=(size, 5, 2560)).astype(np.float32) * 2.0
    data = cp.asarray(data_host, dtype=np.float32)

    # run code and time it
    # cold run first
    paganin_filter_savu(
        data,
        ratio=250.0,
        energy=53.0,
        distance=1.0,
        resolution=1.28,
        pad_y=100,
        pad_x=100,
        pad_method="edge",
        increment=0.0,
    )
    dev = cp.cuda.Device()
    dev.synchronize()

    start = time.perf_counter_ns()
    nvtx.RangePush("Core")
    for _ in range(10):
        paganin_filter_savu(
            data,
            ratio=250.0,
            energy=53.0,
            distance=1.0,
            resolution=1.28,
            pad_y=100,
            pad_x=100,
            pad_method="edge",
            increment=0.0,
        )
    nvtx.RangePop()
    dev.synchronize()
    duration_ms = float(time.perf_counter_ns() - start) * 1e-6 / 10

    assert "performance in ms" == duration_ms


@cp.testing.gpu
def test_paganin_filter_1D_raises(ensure_clean_memory):
    _data = cp.ones(10)
    with pytest.raises(ValueError):
        paganin_filter_savu(_data)

    _data = None  #: free up GPU memory


@cp.testing.gpu
def test_paganin_filter_tomopy_1D_raises(ensure_clean_memory):
    _data = cp.ones(10)
    with pytest.raises(ValueError):
        paganin_filter_tomopy(_data)

    _data = None  #: free up GPU memory


@cp.testing.gpu
def test_paganin_filter_tomopy(data):
    # --- testing the Paganin filter from TomoPy on tomo_standard ---#
    filtered_data = paganin_filter_tomopy(data).get()

    assert filtered_data.ndim == 3
    assert_allclose(np.mean(filtered_data), -6.74213, rtol=eps)
    assert_allclose(np.max(filtered_data), -6.496699, rtol=eps)

    #: make sure the output is float32
    assert filtered_data.dtype == np.float32


@cp.testing.gpu
def test_paganin_filter2_energy100(data):
    filtered_data = paganin_filter_tomopy(data, energy=100.0).get()

    assert_allclose(np.mean(filtered_data), -6.73455, rtol=1e-05)
    assert_allclose(np.min(filtered_data), -6.909582, rtol=eps)

    assert filtered_data.ndim == 3
    assert filtered_data.dtype == np.float32


@cp.testing.gpu
def test_paganin_filter2_dist75(data):
    filtered_data = paganin_filter_tomopy(data, dist=75.0, alpha=1e-6).get()

    assert_allclose(np.sum(np.mean(filtered_data, axis=(1, 2))), -1215.4985, rtol=1e-6)
    assert_allclose(np.sum(filtered_data), -24893412., rtol=1e-6)
    assert_allclose(np.mean(filtered_data[0, 60:63, 90]), -6.645878, rtol=1e-6)
    assert_allclose(np.sum(filtered_data[50:100, 40, 1]), -343.5908, rtol=1e-6)


@cp.testing.gpu
@pytest.mark.perf
def test_paganin_filter2_performance(ensure_clean_memory):
    # Note: low/high and size values taken from sample2_medium.yaml real run

    # this test needs ~20GB of memory with 1801 - we'll divide depending on GPU memory
    dev = cp.cuda.Device()
    mem_80percent = 0.8 * dev.mem_info[0]
    size = 1801
    required_mem = 20 * 1024 * 1024 * 1024
    if mem_80percent < required_mem:
        size = int(np.ceil(size / required_mem * mem_80percent))
        print(f"Using smaller size of ({size}, 5, 2560) due to memory restrictions")

    data_host = np.random.random_sample(size=(size, 5, 2560)).astype(np.float32) * 2.0
    data = cp.asarray(data_host, dtype=np.float32)

    # run code and time it
    # cold run first
    paganin_filter_tomopy(data)
    dev = cp.cuda.Device()
    dev.synchronize()

    start = time.perf_counter_ns()
    nvtx.RangePush("Core")
    for _ in range(10):
        paganin_filter_tomopy(data)

    nvtx.RangePop()
    dev.synchronize()
    duration_ms = float(time.perf_counter_ns() - start) * 1e-6 / 10

    assert "performance in ms" == duration_ms


@cp.testing.gpu
@pytest.mark.parametrize("slices", [15, 51, 160])
@pytest.mark.parametrize("dtype", [np.uint16, np.float32])
def test_paganin_filter2_meta(slices, dtype, ensure_clean_memory):
    cache = cp.fft.config.get_plan_cache()
    cache.clear()
    kwargs = {}
    data = cp.random.random_sample((slices, 111, 121), dtype=np.float32)
    if dtype == np.uint16:
        data = cp.asarray(data * 300.0, dtype=np.uint16)
    hook = MaxMemoryHook(data.size * data.itemsize)
    with hook:
        paganin_filter_tomopy(data, **kwargs)

    assert paganin_filter_tomopy.meta.pattern == 'projection'
    assert 'paganin_filter_tomopy' in method_registry['httomolibgpu']['prep']['phase']
    assert not paganin_filter_tomopy.meta.cpu
    assert paganin_filter_tomopy.meta.gpu

    # make sure estimator function is within range (80% min, 100% max)
    max_mem = hook.max_mem
    actual_slices = data.shape[0]
    estimated_slices, dtype_out, output_dims = paganin_filter_tomopy.meta.calc_max_slices(
        0,
        (data.shape[1], data.shape[2]),
        data.dtype, max_mem, **kwargs)
    assert estimated_slices <= actual_slices
    assert estimated_slices / actual_slices >= 0.8
