import tvm
from tvm.contrib import nvcc_compiler
from tvm.contrib import metal_compiler
import numpy as np
import time

#@tvm.register_func
def tvm_callback_metal_compile(code):
    lib = metal_compiler.compile_source(code)
    return lib

def test_gemm():
    # graph
    nn = 1024
    n = tvm.var('n')
    n = tvm.convert(nn)
    m = n
    l = n
    A = tvm.placeholder((n, l), name='A')
    B = tvm.placeholder((m, l), name='B')
    k = tvm.reduce_axis((0, l), name='k')
    C = tvm.compute(
        (n, m),
        lambda ii, jj: tvm.sum(A[ii, k] * B[jj, k], axis=k),
        name='CC')
    # schedule
    s = tvm.create_schedule(C.op)
    xtile, ytile = 32, 32
    scale = 8
    num_thread = 8
    block_factor = scale * num_thread
    block_x = tvm.thread_axis("blockIdx.x")
    thread_x = tvm.thread_axis("threadIdx.x")
    block_y = tvm.thread_axis("blockIdx.y")
    thread_y = tvm.thread_axis("threadIdx.y")

    CC = s.cache_write(C, "local")
    AA = s.cache_read(A, "shared", [CC])
    BB = s.cache_read(B, "shared", [CC])
    by, yi = s[C].split(C.op.axis[0], factor=block_factor)
    bx, xi = s[C].split(C.op.axis[1], factor=block_factor)
    s[C].reorder(by, bx, yi, xi)
    s[C].bind(by, block_y)
    s[C].bind(bx, block_x)
    ty, yi = s[C].split(yi, nparts=num_thread)
    tx, xi = s[C].split(xi, nparts=num_thread)
    s[C].reorder(ty, tx, yi, xi)
    s[C].bind(ty, thread_y)
    s[C].bind(tx, thread_x)
    yo, xo = CC.op.axis
    s[CC].reorder(k, yo, xo)


    s[CC].compute_at(s[C], tx)
    s[AA].compute_at(s[CC], k)
    s[BB].compute_at(s[CC], k)

    ty, xi = s[AA].split(s[AA].op.axis[0], nparts=num_thread)
    tx, xi = s[AA].split(xi, nparts=num_thread)
    s[AA].bind(ty, thread_y)
    s[AA].bind(tx, thread_x)

    ty, xi = s[BB].split(s[BB].op.axis[0], nparts=num_thread)
    tx, xi = s[BB].split(xi, nparts=num_thread)
    s[BB].bind(ty, thread_y)
    s[BB].bind(tx, thread_x)

    max_auto_unroll_step = 0
    # lowering test
    s = s.normalize()

    # one line to build the function.
    def check_device(device):
        if not tvm.module.enabled(device):
            print("skip because %s is not enabled.." % device)
            return

        f = tvm.build(s, [A, B, C], device,
                      max_auto_unroll_step=max_auto_unroll_step)
        ctx = tvm.context(device, 0)
        # launch the kernel.
        n = nn
        m = n
        l = n
        a_np = np.random.uniform(size=(n, l)).astype(A.dtype)
        b_np = np.random.uniform(size=(m, l)).astype(B.dtype)
        a = tvm.nd.array(a_np, ctx)
        b = tvm.nd.array(b_np, ctx)
        c = tvm.nd.array(np.zeros((n, m), dtype=C.dtype), ctx)
        f(a, b, c)
        ctx.sync()
        tbegin = time.time()
        f(a, b, c)
        tpush = time.time()
        ctx.sync()
        tend = time.time()
        print("launch=%g sec, exec=%g sec" % (tpush - tbegin, tend - tbegin))
        np.testing.assert_allclose(
            c.asnumpy(), np.dot(a_np, b_np.T), rtol=1e-5)

    check_device("metal")
    check_device("opencl")
    check_device("cuda")

if __name__ == "__main__":
    test_gemm()
