# P218-N02 - arm defects found while validating the consistency gate

This note records what did **not** work, with the numbers that show it. It is
part of the deliverable: the gate is only as good as the arms that feed it, and
the two arms of the shipped case are in very different states.

## 1. Lattice-Boltzmann arm

### 1.1 Defect found and fixed: direction 0 was never streamed

`stream()` looped over directions `1..8` only. The rest population `f_0` has a
zero lattice velocity, so it does not move - but it still has to be copied from
the post-collision state into the next state. With a pre-allocated buffer the
copy was silently missing, so `f_0` kept the value from two steps earlier.
Measured symptom (D=12 lattice units, U=0.1, 4 steps, node adjacent to the
obstacle): the density at that node fell to `rho = 0.48` while every other node
stayed at `1.0`, and the population vector carried `f_0 = 0` where the
equilibrium value is `4/9 * rho`. Fixed by looping over all nine directions;
the kernel diagnostics (Taylor viscosity error 0.7 %, channel profile L2
error 0.09 % at 32 cells) were unaffected because those tests are fully periodic
and never touch a wall.

### 1.2 Defect still open: obstacle force and wall-adjacent conditioning

After the fix above the kernel is stable in the analytic tests, but the
obstacle case is not usable yet:

* at 500 steps (D=12, U=0.1, 20D x 12D) the momentum-exchange sum gives
  `C_D = -5e-4`, i.e. the front and back of the obstacle contribute almost
  exactly equal and opposite momentum - no wake has formed;
* between 500 and 1000 steps the field diverges to NaN;
* the same happens with the plain half-way bounce-back and with the Bouzidi
  interpolation, so the defect is in the inlet/outlet coupling or in the wall
  linkage bookkeeping, not in the closure.

The Bouzidi link table is built by rolling the solid mask, and a wrap-around
link (periodic transverse direction) would produce a wall fraction computed from
an analytic distance that does not correspond to the wrapped geometry. That is
the first place to look next; it is recorded here rather than worked around,
because an uncertified arm must not enter a consistency report.

Consequence: the shipped case report has **no LBM arm**, therefore
`missing_solver_families = ["LBM"]` and the overall verdict is `withheld`.
The acceptance criterion "LBM versus FVM force coefficient within 5 %" is **not
met** and is reported as FAIL.

## 2. Body-fitted (log-polar vorticity/stream-function) arm

The arm converges and is reproducible, but it does not reproduce the physics of
the injected case at Re = 100:

| quantity | measured | expected (reference / potential flow) |
|---|---|---|
| `C_D` total | 0.735 | 1.34 (Dennis & Chang 1970) |
| `C_D` pressure part | 0.497 | ~1.0 |
| `C_D` friction part | 0.238 | ~0.34 |
| `C_p` minimum | -0.24 | about -1.2 |
| maximum azimuthal velocity | 1.06 U | 1.5-1.8 U |
| recirculation length | 4.4 D | about 0.9 D |

The velocity field never accelerates around the obstacle, so the solution
behaves like a much higher Reynolds number with a weak pressure signature. The
grid operators themselves are certified (analytic potential flow: relative
Laplacian error 4.7e-5, Fourier Poisson versus assembled sparse solve 1.6e-9,
manufactured solutions solved to 6.5e-15, Bernoulli pressure with the analytic
Neumann data recovered to 1.3e-14), so the defect is in the time-marching
coupling - convection scheme plus wall-vorticity closure - not in the operators.

### 2.1 Measured instability of the second-order wall-vorticity closure

While debugging the coupling, the feedback gain of the wall-vorticity closure
was measured directly (perturb one near-wall cell by 1, re-solve the
stream-function Poisson, read the new wall vorticity):

| closure | gain `d(omega_wall)/d(omega_cell)` | behaviour |
|---|---|---|
| Thom, first order `-2 psi_1/h^2` | 0.61 | stable fixed point |
| second order `(psi_2 - 8 psi_1)/(2 h^2)` | 1.13 | grows ~30 % per step |

That is why the shipped arm marches with the first-order Thom closure even
though the second-order one is in the code and documented: the explicit
Dirichlet/Neumann coupling of a stream-function/vorticity scheme amplifies the
second-order extrapolation. This is reusable knowledge for any arm of this
family.

### 2.2 A sign convention that cost real time

The Fourier Poisson helper solves `alpha f - beta laplacian(f) = rhs` with the
rows scaled by `r^2`. Passing `-omega * r^2` for the stream-function equation
(`laplacian(psi) = -omega`) flips the sign of the whole vorticity feedback and
the field grows by a factor of two every few steps. The correct right-hand side
is `+omega * r^2`. The failure looks exactly like a physical instability, so the
sign of a Poisson right-hand side must be certified by a manufactured solution
before the time loop is trusted - which is what the test suite now does.