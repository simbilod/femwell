# https://www.fiberoptics4sale.com/blogs/wave-optics/coupled-mode-theory
# https://www.fiberoptics4sale.com/blogs/wave-optics/two-mode-coupling

from collections import OrderedDict
from shapely.geometry import Polygon
import numpy as np
import matplotlib.pyplot as plt
from skfem import Mesh, Basis, ElementTriP0

from femwell.mesh import mesh_from_polygons
from femwell.mode_solver import compute_modes, plot_mode, calculate_overlap, calculate_hfield, \
    calculate_coupling_coefficient

w_sim = 4
h_clad = 1
h_box = 1
w_core_1 = 0.45
w_core_2 = 0.5
gap = .5
h_core = 0.22
offset_heater = 2.2
h_heater = .14
w_heater = 2

wavelength = 1.55
k0 = 2 * np.pi / wavelength

polygons = OrderedDict(
    core_1=Polygon([
        (-w_core_1 - gap / 2, 0),
        (-w_core_1 - gap / 2, h_core),
        (-gap / 2, h_core),
        (-gap / 2, 0),
    ]),
    core_2=Polygon([
        (w_core_2 + gap / 2, 0),
        (w_core_2 + gap / 2, h_core),
        (gap / 2, h_core),
        (gap / 2, 0),
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
    core_1={"resolution": 0.03, "distance": 1},
    core_2={"resolution": 0.03, "distance": 1}
)

mesh_from_polygons(polygons, resolutions, filename='mesh.msh', default_resolution_max=.2)

mesh = Mesh.load('mesh.msh')
basis0 = Basis(mesh, ElementTriP0(), intorder=4)
epsilon = basis0.zeros()
epsilon[basis0.get_dofs(elements='core_1')] = 3.4777 ** 2
epsilon[basis0.get_dofs(elements='core_2')] = 1.444 ** 2
epsilon[basis0.get_dofs(elements='clad')] = 1.444 ** 2
epsilon[basis0.get_dofs(elements='box')] = 1.444 ** 2
basis0.plot(epsilon, colorbar=True).show()

lams_1, basis, xs_1 = compute_modes(basis0, epsilon, wavelength=wavelength, mu_r=1, num_modes=1)
print(lams_1)

plot_mode(basis, np.real(xs_1[0]))
plt.show()

epsilon_2 = basis0.zeros()
epsilon_2[basis0.get_dofs(elements='core_1')] = 1.444 ** 2
epsilon_2[basis0.get_dofs(elements='core_2')] = 3.4777 ** 2
epsilon_2[basis0.get_dofs(elements='clad')] = 1.444 ** 2
epsilon_2[basis0.get_dofs(elements='box')] = 1.444 ** 2
basis0.plot(epsilon_2, colorbar=True).show()

lams_2, basis, xs_2 = compute_modes(basis0, epsilon_2, wavelength=wavelength, mu_r=1, num_modes=1)
print(lams_2)

plot_mode(basis, np.real(xs_2[0]))
plt.show()

epsilons = [epsilon, epsilon_2]
modes = [(lam, x, 0) for lam, x in zip(lams_1, xs_1)] + [(lam, x, 1) for lam, x in zip(lams_2, xs_2)]

overlap_integrals = np.zeros((len(modes), len(modes)), dtype=complex)
for i, (lam_i, E_i, epsilon_i) in enumerate(modes):
    for j, (lam_j, E_j, epsilon_j) in enumerate(modes):
        H_i = calculate_hfield(basis, E_i, -lam_i * (2 * np.pi / 1.55))
        H_j = calculate_hfield(basis, E_j, -lam_j * (2 * np.pi / 1.55))
        overlap_integrals[i, j] = calculate_overlap(basis, E_i, H_i, E_j, H_j)

print(overlap_integrals)
plt.imshow(np.abs(overlap_integrals))
plt.colorbar()
plt.show()

coupling_coefficients = np.zeros((len(modes), len(modes)), dtype=complex)
for i, (lam_i, E_i, epsilon_i) in enumerate(modes):
    for j, (lam_j, E_j, epsilon_j) in enumerate(modes):
        coupling_coefficients[i, j] = k0 * calculate_coupling_coefficient(basis0,
                                                                          epsilons[(epsilon_j + 1) % 2] - 1.444 ** 2,
                                                                          basis, E_i, E_j)

print(coupling_coefficients)
plt.imshow(np.abs(coupling_coefficients))
plt.colorbar()
plt.show()

kappas = np.array([[(coupling_coefficients[i, j] - overlap_integrals[i, (i + 1) % 2] * coupling_coefficients[
    (i + 1) % 2, j] / overlap_integrals[
                         (i + 1) % 2, (i + 1) % 2]) / (1 - overlap_integrals[0, 1] * overlap_integrals[1, 0] / (
        overlap_integrals[0, 0] * overlap_integrals[1, 1])) for i in range(2)] for j in
                   range(2)])
print(kappas)

delta = 0.5 * (np.real(lams_1[0]) * k0 + kappas[1, 1] - (np.real(lams_2[0]) * k0 + kappas[0, 0]))
print(delta, np.real(lams_1[0]) * k0, kappas[1, 1])

beta_c = (kappas[0, 1] * kappas[1, 0] + delta ** 2) ** .5

print(np.pi / (2 * beta_c))

eta = np.abs(kappas[1, 0] ** 2 / beta_c ** 2) * np.sin(beta_c * 1e3)
print(eta, np.abs(kappas[1, 0] ** 2 / beta_c ** 2))