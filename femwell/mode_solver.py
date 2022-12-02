"""Waveguide analysis based on https://doi.org/10.1080/02726340290084012."""
import matplotlib.pyplot as plt
import numpy as np
import scipy.sparse.linalg

from skfem import BilinearForm, Basis, ElementTriN1, ElementTriN2, ElementDG, ElementTriP0, ElementTriP1, \
    ElementTriP2, ElementVector, Mesh, Functional, LinearForm, condense, solve
from skfem.helpers import curl, grad, dot, inner, cross

from collections.abc import Iterable
from itertools import permutations


def compute_modes(basis_epsilon_r, epsilon_r, wavelength, mu_r, num_modes, order=1):
    k0 = 2 * np.pi / wavelength

    if order == 1:
        element = ElementTriN1() * ElementTriP1()
    elif order == 2:
        element = ElementTriN2() * ElementTriP2()
    else:
        raise AssertionError('Only order 1 and 2 implemented by now.')

    basis = basis_epsilon_r.with_element(element)
    basis_epsilon_r = basis.with_element(basis_epsilon_r.elem)  # adjust quadrature

    @BilinearForm(dtype=epsilon_r.dtype)
    def aform(e_t, e_z, v_t, v_z, w):
        return 1 / mu_r * curl(e_t) * curl(v_t) \
               - k0 ** 2 * w['epsilon'] * dot(e_t, v_t) \
               + 1 / mu_r * dot(grad(e_z), v_t) \
               + w['epsilon'] * inner(e_t, grad(v_z)) - w['epsilon'] * e_z * v_z

    @BilinearForm(dtype=epsilon_r.dtype)
    def bform(e_t, e_z, v_t, v_z, w):
        return - 1 / mu_r * dot(e_t, v_t)

    A = aform.assemble(basis, epsilon=basis_epsilon_r.interpolate(epsilon_r))
    B = bform.assemble(basis, epsilon=basis_epsilon_r.interpolate(epsilon_r))

    def solver_slepc(A,B):
        from petsc4py import PETSc
        from slepc4py import SLEPc

        A_ = PETSc.Mat().createAIJ(size=A.shape, csr=(A.indptr, A.indices, A.data))
        B_ = PETSc.Mat().createAIJ(size=B.shape, csr=(B.indptr, B.indices, B.data))

        eps = SLEPc.EPS().create()
        eps.setOperators(A_, B_)
        eps.setType(SLEPc.EPS.Type.KRYLOVSCHUR)
        eps.getST().setType(SLEPc.ST.Type.SINVERT)
        eps.setWhichEigenpairs(SLEPc.EPS.Which.TARGET_MAGNITUDE)
        eps.setTarget(k0 ** 2 * np.max(epsilon_r) ** 2)
        eps.setDimensions(num_modes)
        eps.solve()

        xr, wr = A_.getVecs()
        xi, wi = A_.getVecs()
        lams, xs = [], []
        for i in range(eps.getConverged()):
            lams.append(eps.getEigenpair(i, xr, xi))
            xs.append(np.array(xr) + 1j * np.array(xi))

        xs = np.array(xs, dtype=np.complex128)
        print(xs.dtype)
        lams = np.array(lams)
        return lams, xs.T

    lams, xs = solve(*condense(A,B,D=basis.get_dofs()), solver=solver_slepc)
    print(xs.dtype)
    xs = xs.T
    print(xs.dtype)
    print(lams[:, np.newaxis].dtype, xs[:, basis.split_indices()[1]].dtype)
    xs[:, basis.split_indices()[1]] /= 1j * np.sqrt(lams[:, np.newaxis])  # undo the scaling E_3,new = beta * E_3

    for i, lam in enumerate(lams):
        H = calculate_hfield(basis, xs[i], np.sqrt(lam))
        xs[i] /= np.sqrt(calculate_overlap(basis, xs[i], H, basis, xs[i], H))

    return np.sqrt(lams)[:num_modes] / k0, basis, xs[:num_modes]


def calculate_hfield(basis, xs, beta):
    xs = xs.astype(complex)

    @BilinearForm(dtype=np.complex64)
    def aform(e_t, e_z, v_t, v_z, w):
        return (-1j * beta * e_t[1] + e_z.grad[1]) * v_t[0] \
               + (1j * beta * e_t[0] - e_z.grad[0]) * v_t[1] \
               + e_t.curl * v_z

    a_operator = aform.assemble(basis)

    @BilinearForm(dtype=np.complex64)
    def bform(e_t, e_z, v_t, v_z, w):
        return dot(e_t, v_t) + e_z * v_z

    b_operator = bform.assemble(basis)

    return scipy.sparse.linalg.spsolve(b_operator, a_operator @ xs) * -1j


def calculate_energy_current_density(basis, xs):
    basis_energy = basis.with_element(ElementTriP0())

    @LinearForm(dtype=complex)
    def aform(v, w):
        e_t, e_z = w['e']
        return abs(e_t[0]) ** 2 * v + abs(e_t[1]) ** 2 * v + abs(e_z) * v

    a_operator = aform.assemble(basis_energy, e=basis.interpolate(xs))

    @BilinearForm(dtype=complex)
    def bform(e, v, w):
        return e * v

    b_operator = bform.assemble(basis_energy)

    return basis_energy, scipy.sparse.linalg.spsolve(b_operator, a_operator)


def calculate_overlap(basis_i, E_i, H_i, basis_j, E_j, H_j):
    @Functional
    def overlap(w):
        return cross(np.conj(w['E_i'][0]), w['H_j'][0]) + cross(w['E_j'][0], np.conj(w['H_i'][0]))

    if basis_i == basis_j:
        return 0.5 * overlap.assemble(basis_i, E_i=basis_i.interpolate(E_i), H_i=basis_i.interpolate(H_i),
                                      E_j=basis_j.interpolate(E_j), H_j=basis_j.interpolate(H_j))
    else:

        basis_j_fix = basis_j.with_element(ElementVector(ElementTriP1()))

        (et, et_basis), (ez, ez_basis) = basis_j.split(E_j)
        E_j = basis_j_fix.project(et_basis.interpolate(et), dtype=np.cfloat)
        (et_x, et_x_basis), (et_y, et_y_basis) = basis_j_fix.split(E_j)

        (et, et_basis), (ez, ez_basis) = basis_j.split(H_j)
        H_j = basis_j_fix.project(et_basis.interpolate(et), dtype=np.cfloat)
        (ht_x, ht_x_basis), (ht_y, ht_y_basis) = basis_j_fix.split(H_j)

        @Functional(dtype=np.complex64)
        def overlap(w):
            return cross(np.conj(w['E_i'][0]),
                         np.array((ht_x_basis.interpolator(ht_x)(w.x), ht_y_basis.interpolator(ht_y)(w.x)))) \
                   + cross(np.array((et_x_basis.interpolator(et_x)(w.x), et_y_basis.interpolator(et_y)(w.x))),
                           np.conj(w['H_i'][0]))

        return 0.5 * overlap.assemble(basis_i, E_i=basis_i.interpolate(E_i), H_i=basis_i.interpolate(H_i))


def calculate_scalar_product(basis_i, E_i, basis_j, H_j):
    @Functional
    def overlap(w):
        return cross(np.conj(w['E_i'][0]), w['H_j'][0])

    if basis_i == basis_j:
        return overlap.assemble(basis_i, E_i=basis_i.interpolate(E_i), H_j=basis_j.interpolate(H_j))
    else:

        basis_j_fix = basis_j.with_element(ElementVector(ElementTriP1()))

        (et, et_basis), (ez, ez_basis) = basis_j.split(H_j)
        H_j = basis_j_fix.project(et_basis.interpolate(et), dtype=np.cfloat)
        (ht_x, ht_x_basis), (ht_y, ht_y_basis) = basis_j_fix.split(H_j)

        @Functional(dtype=np.complex64)
        def overlap(w):
            return cross(np.conj(w['E_i'][0]),
                         np.array((ht_x_basis.interpolator(ht_x)(w.x), ht_y_basis.interpolator(ht_y)(w.x))))

        return overlap.assemble(basis_i, E_i=basis_i.interpolate(E_i))


def calculate_coupling_coefficient(basis_epsilon, delta_epsilon, basis, E_i, E_j):
    @Functional
    def overlap(w):
        return w['delta_epsilon'] * (dot(np.conj(w['E_i'][0]), w['E_j'][0]) + np.conj(w['E_i'][1]) * w['E_j'][1])

    return overlap.assemble(basis, E_i=basis.interpolate(E_i), E_j=basis.interpolate(E_j),
                            delta_epsilon=basis_epsilon.interpolate(delta_epsilon))

def plot_mode(basis, mode, plot_vectors=False, colorbar=True, title='E', direction='y', field_components='x'):
    from mpl_toolkits.axes_grid1 import make_axes_locatable

    (et, et_basis), (ez, ez_basis) = basis.split(mode)

    if plot_vectors:
        rc = (2, 1) if direction != 'x' else (1, 2)
        fig, axs = plt.subplots(*rc, subplot_kw=dict(aspect=1))
        for ax in axs:
            for subdomain in basis.mesh.subdomains.keys() - {'gmsh:bounding_entities'}:
                basis.mesh.restrict(subdomain).draw(ax=ax, boundaries_only=True)
        et_basis.plot(et, ax=axs[0])
        ez_basis.plot(ez, ax=axs[1])

        divider = make_axes_locatable(axs[1])
        cax = divider.append_axes("right", size="5%", pad=0.05)
        plt.colorbar(axs[1].collections[0], cax=cax)
        return fig, axs

    plot_basis = et_basis.with_element(ElementVector(ElementDG(ElementTriP1())))
    et_xy = plot_basis.project(et_basis.interpolate(et))
    (et_x, et_x_basis), (et_y, et_y_basis) = plot_basis.split(et_xy)

    rc = (len(field_components), 1) if direction != 'x' else (1, len(field_components))
    if rc == (1,1):
        fig, axs = plt.subplots(1, 1, subplot_kw=dict(aspect=1))
        axs = [axs]
    else:
        fig, axs = plt.subplots(*rc, subplot_kw=dict(aspect=1))
    for ax in axs:
        for subdomain in basis.mesh.subdomains.keys() - {'gmsh:bounding_entities'}:
            basis.mesh.restrict(subdomain).draw(ax=ax, boundaries_only=True)

    for ax, component in zip(axs, field_components):
        ax.set_title(f'${title}_{component}$')
    ax_i = 0
    if 'x' in field_components:
        et_x_basis.plot(et_x, shading='gouraud', ax=axs[ax_i])  # , vmin=np.min(mode), vmax=np.max(mode))
        ax_i += 1
    if 'y' in field_components:
        et_y_basis.plot(et_y, shading='gouraud', ax=axs[ax_i])  # , vmin=np.min(mode), vmax=np.max(mode))
        ax_i += 1
    if 'z' in field_components:
        ez_basis.plot(ez, shading='gouraud', ax=axs[ax_i])  # , vmin=np.min(mode), vmax=np.max(mode))
    if field_components not in [combo for i in range(1, len(field_components) + 1) for combo in permutations(field_components, i) ]:
        raise ValueError("\"field_components\" argument must be string containing x and/or y and/or z.")

    if colorbar:
        if colorbar == 'same':
            plt.colorbar(axs[-1].collections[0], ax=axs.ravel().tolist())
        else:
            for ax in axs:
                divider = make_axes_locatable(ax)
                cax = divider.append_axes("right", size="5%", pad=0.05)
                plt.colorbar(ax.collections[0], cax=cax)

    return fig, axs


if __name__ == "__main__":
    from shapely.geometry import Polygon
    from collections import OrderedDict
    from femwell.mesh import mesh_from_OrderedDict

    w_sim = 3
    h_clad = .7
    h_box = .5
    w_core = 0.5
    h_core = 0.22
    offset_heater = 2.2
    h_heater = .14
    w_heater = 2

    polygons = OrderedDict(
        core=Polygon([
            (-w_core / 2, 0),
            (-w_core / 2, h_core),
            (w_core / 2, h_core),
            (w_core / 2, 0),
        ]),
        clad=Polygon([
            (-w_sim / 2, 0),
            (-w_sim / 2, h_clad),
            (w_sim / 2, h_clad),
            (w_sim / 2, 0),
        ]),
        box=Polygon([
            (-w_sim / 2, 0),
            (-w_sim / 2, - h_box),
            (w_sim / 2, - h_box),
            (w_sim / 2, 0),
        ])
    )

    resolutions = dict(
        core={"resolution": 0.05, "distance": 1}
    )

    mesh_from_OrderedDict(polygons, resolutions, filename='mesh.msh', default_resolution_max=.2)

    mesh = Mesh.load('mesh.msh')
    basis = Basis(mesh, ElementTriN2() * ElementTriP2())
    basis0 = basis.with_element(ElementTriP0())
    epsilon = basis0.zeros(dtype=complex)
    epsilon[basis0.get_dofs(elements='core')] = 3.4777 ** 2
    epsilon[basis0.get_dofs(elements='clad')] = 1.444 ** 2
    epsilon[basis0.get_dofs(elements='box')] = 1.444 ** 2
    # basis0.plot(epsilon, colorbar=True).show()

    lams, basis, xs = compute_modes(basis0, epsilon, wavelength=1.55, mu_r=1, num_modes=6, order=2)

    print(lams)

    plot_mode(basis, np.real(xs[0]))
    plt.show()
    plot_mode(basis, np.imag(xs[0]))
    plt.show()

    xbs = calculate_hfield(basis, xs[0], lams[0] * (2 * np.pi / 1.55))

    plot_mode(basis, np.real(xbs))
    plt.show()
    plot_mode(basis, np.imag(xbs))
    plt.show()

    integrals = np.zeros((len(lams),) * 2, dtype=complex)
    for i in range(len(lams)):
        for j in range(len(lams)):
            E_i = xs[i]
            E_j = xs[j]
            H_i = calculate_hfield(basis, E_i, lams[i] * (2 * np.pi / 1.55))
            H_j = calculate_hfield(basis, E_j, lams[j] * (2 * np.pi / 1.55))
            integrals[i, j] = calculate_overlap(basis, E_i, H_i, basis, E_j, H_j)

    plt.imshow(np.real(integrals))
    plt.colorbar()
    plt.show()
