| 模块 | 文件数 | 行数 |
|---|---|---|
| `src/core` | 189 | 111,236 |
| `src/lbm` | 202 | 54,126 |
| `apps/benchmarks` | 82 | 32,795 |
| `src/mesa_pd` | 129 | 24,018 |
| `apps/showcases` | 59 | 22,881 |
| `src/blockforest` | 67 | 21,654 |
| `src/lbm_generated` | 51 | 21,278 |
| `src/field` | 75 | 18,427 |
| `tests/lbm` | 59 | 16,584 |
| `tests/mesa_pd` | 87 | 11,373 |
| `src/geometry` | 70 | 10,452 |
| `tests/lbm_mesapd_coupling` | 20 | 10,362 |

| 能力探针（代码级 grep） | 结果 | 首个证据 |
|---|---|---|
| `shan_chen_pseudopotential` | ❌ 0 命中（本仓无） | — |
| `mcmp_multicomponent` | ❌ 0 命中（本仓无） | — |
| `free_energy` | ❌ 0 命中（本仓无） | — |
| `phase_field_allen_cahn` | ✅ 24 处命中 | apps/benchmarks/PhaseFieldAllenCahn/multiphase_codegen.py:10:from lbmpy.phasefield_allen_cahn.kernel_equations import initializer_kernel_phase_field_lb, \ |
| `free_surface_module` | ✅ 1 处命中 | src/lbm/free_surface |
| `kbc_or_recursive_collision` | ✅ 1 处命中 | apps/benchmarks/UniformGridGPU/old_ideas/UniformGridGPU.py:60:        'method': 'trt-kbc-n4', |
| `entropic` | ✅ 15 处命中 | src/lbm/free_surface/dynamics/PdfRefillingSweep.h:391: *             - equation (10) in Dorscher et al., "Grad’s approximation for moving and stationary walls in entropic |
| `zou_he` | ❌ 0 命中（本仓无） | — |
| `aa_pattern_even_odd` | ✅ 34 处命中 | python/lbmpy_walberla/additional_data_handler.py:380:            self._zeroth_timestep = Timestep.EVEN if is_inplace(self._streaming_pattern) else Timestep.BOTH |
| `gpu_target_codegen` | ✅ 87 处命中 | src/lbm_generated/boundary/boundary_generation_script.py:17:        target = Target.GPU if ctx.cuda else Target.CPU |
| `metis_partitioning` | ✅ 288 处命中 | src/blockforest/GlobalLoadBalancing.h:56:   class MetisConfiguration { |
| `ibm_partially_saturated_cells` | ✅ 1 处命中 | src/lbm_mesapd_coupling/partially_saturated_cells_method |
| `momentum_exchange` | ✅ 1 处命中 | src/lbm_mesapd_coupling/momentum_exchange_method |
| `amr_refinement` | ✅ 1 处命中 | src/lbm/refinement |
| `curved_bb` | ✅ 5 处命中 | src/lbm/boundary/Curved.h:46:// delta = "distance to wall" / "distance between cell centers" |
| `ci_workflows` | ✅ 1 处命中 | .github/workflows/basicTests.yml |
| `analytic_validation` | ✅ 189 处命中 | src/field/AccuracyEvaluation.h:16://! \file AccuracyEvaluation.h |
| `stencils` | ✅ 8 处命中 | src/stencil/D2Q4.h |
| `collision_models` | ✅ 5 处命中 | src/lbm/lattice_model/CollisionModel.h:80 class SRT |
| `force_models` | ✅ 7 处命中 | src/lbm/lattice_model/ForceModel.h:142 class None |
