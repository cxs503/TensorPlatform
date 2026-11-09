"""Evidence registry for the ThermalMultiphaseOpenLB OSS inventory.

Every pointer in ``docs/oss_inventory/OSS-INV-ThermalMultiphaseOpenLB.md`` is
declared here as ``(relative path, needle[, occurrence])`` and is resolved to a
``path:line`` at render time; a needle that is missing *or ambiguous* is a hard
build failure, so no line number in the report is ever typed by hand.

The needles are deliberately short, real substrings of the analysed sources.
``occurrence`` (0-based) is only used where the same statement legitimately
appears more than once in a file (e.g. the two temperature-dependent
``operator()`` overloads of the same EOS).
"""

from __future__ import annotations

REPO = "JuliusWeinmiller/ThermalMultiphaseOpenLB"
COMMIT = "769efd471ac903dd8c70feb0369d2fc810f5561a"
COMMIT_DATE = "2026-09-12"

SRC = "phaseChangeExtension/phaseChangeSource/"
EX = "phaseChangeExtension/phaseChangeExamples/"
PY = "phaseChangeExtension/EquationOfStatePython/"

# key -> (path, needle) | (path, needle, occurrence) | [(path, needle), ...]
REFS: dict[str, object] = {
    # ---------------- README / provenance / licence ----------------
    "readme_intro": ("README.md", "thermal single-component phase change multiphase flow"),
    "readme_thesis": ("README.md", "as part of his master thesis"),
    "readme_thesis_link": ("README.md", "resolver.tudelft.nl"),
    "readme_high_ratio": ("README.md", "at high density ratios"),
    "readme_forcing_bullet": ("README.md", "A new forcing algorithm for pseudopotential method"),
    "readme_thermo_bullet": ("README.md", "thermodynamic consistent densities"),
    "readme_latent_bullet": ("README.md", "Incorporation of latent heat via the equation of state"),
    "readme_semihybrid_bullet": ("README.md", "Semi-hybrid thermal solver"),
    "readme_limit_version": ("README.md", "The original extension was created for OpenLB v1.3-1"),
    "readme_no14": ("README.md", "not working in combination with OpenLB v 1.4"),
    "readme_install_drop": ("README.md", "Drop the `phaseChangeExtension` folder into the OpenLB base folder"),
    "readme_install_gsl": ("README.md", "-lgsl -lgslcblas", 0),
    "readme_install_patch": ("README.md", "Change `private` to `protected`"),
    "readme_paper_forcing": ("README.md", "Forcing scheme in pseudopotential lattice Boltzmann model"),
    "readme_paper_gong": ("README.md", "lattice Boltzmann method for simulation of liquid"),
    "readme_paper_li2017": ("README.md", "Improved thermal lattice Boltzmann model for simulation of liquid"),
    "readme_paper_li2013": ("README.md", "Lattice Boltzmann modeling of multiphase flows at large density ratio"),
    "readme_boiling_paper": ("README.md", "How does boiling occur in lattice Boltzmann simulations"),

    # ---------------- extra descriptor fields ----------------
    "field_psi": (SRC + "descriptorFieldExtended.h", "struct PSI_PSEUDO_RHO"),
    "field_phi": (SRC + "descriptorFieldExtended.h", "struct PHI_THERMAL"),
    "field_prevtv": (SRC + "descriptorFieldExtended.h", "struct PREV_T_V"),
    "field_prevphi": (SRC + "descriptorFieldExtended.h", "struct PREV_PHI"),

    # ---------------- EOS interface + implementations ----------------
    "eos_psi_virtual": (SRC + "interactionPotentialExtended.h",
                        "virtual bool operator() (T psi[], const S rho[]) = 0;"),
    "eos_psi_T_virtual": (SRC + "interactionPotentialExtended.h",
                          "virtual bool operator() (T psi[], const S rho[], const S tr[]) = 0;"),
    "eos_dpdt_virtual": (SRC + "interactionPotentialExtended.h",
                         "virtual bool computeDerivativeConstRho"),
    "eos_pressure_hook": (SRC + "interactionPotentialExtended.h",
                          "static double computePressure( double volume, void * params );", 0),
    "eos_param_struct": (SRC + "interactionPotentialExtended.h", "struct staticPressureParameters {"),
    "eos_pr_ctor": (SRC + "interactionPotentialExtended.hh",
                    "PengRobinsonExt<T,S>::PengRobinsonExt(T G, T acentricFactor"),
    "eos_pr_pressure_not": (SRC + "interactionPotentialExtended.hh",
                            "T p = (rho[0]*this->_R*this->_t/(1.-this->_b*rho[0]))", 0),
    "eos_pr_pressure_T": (SRC + "interactionPotentialExtended.hh",
                          "T p = (rho[0]*this->_R*this->_t/(1.-this->_b*rho[0]))", 1),
    "eos_pr_dpdt": (SRC + "interactionPotentialExtended.hh",
                    "deriv[0] = rho[0]*this->_R/(1. - this->_b*rho[0]) - this->_a"),
    "eos_psi_sqrt": (SRC + "interactionPotentialExtended.hh",
                     "psi[0] = sqrt(6.*(p-rho[0]/3.)/this->_G);", 0),
    "eos_cs_psi_noT": (SRC + "interactionPotentialExtended.hh",
                       "T p = rho[0]*this->_R*this->_t*((1.+c+c*c-c*c*c)", 0),
    "eos_cs_psi_T": (SRC + "interactionPotentialExtended.hh",
                     "T p = rho[0]*this->_R*this->_t*((1.+c+c*c-c*c*c)", 1),
    "eos_cs_deriv_comment": (SRC + "interactionPotentialExtended.hh",
                             "This particular derivative is temperature independent"),
    "eos_tc_from_ab": (SRC + "interactionPotentialExtended.hh",
                       "this->_tc = 0.0778/0.45724*this->_a/this->_b/this->_R;"),
    "eos_rhoc_zc307": (SRC + "interactionPotentialExtended.hh",
                       "this->_rhoc = this->_pc/0.307/this->_R/this->_tc;"),
    "eos_rhoc_zc_comment": (SRC + "interactionPotentialExtended.hh", "// From python file: 0.3074"),
    "eos_write_table": (SRC + "interactionPotentialExtended.hh",
                        "InteractionPotentialExtended information"),

    # ---------------- Maxwell construction (C++/GSL) ----------------
    "mw_operator": (SRC + "maxwellConstruction.hh",
                    "bool MaxwellConstruction<T,ipType>::operator() ( T rho[2], T tr)"),
    "mw_root_brent": (SRC + "maxwellConstruction.hh", "gsl_root_fsolver_brent", 1),
    "mw_qags": (SRC + "maxwellConstruction.hh", "gsl_integration_qags(&_local_gsl_fnRoot"),
    "mw_return_densities": (SRC + "maxwellConstruction.hh",
                            "rho[0] = 1./ parametersArea.intersectLeft;"),
    "mw_accuracy": (SRC + "maxwellConstruction.h", "double accuracy = 1e-6;"),
    "mw_gsl_include": (SRC + "maxwellConstruction.h", "#include <gsl/gsl_integration.h>"),
    "mw_min_solver": (SRC + "maxwellConstruction.hh", "gsl_min_fminimizer_iterate(_gsl_fminimizer)"),

    # ---------------- fluid properties ----------------
    "fp_interp": (SRC + "fluidProperties.hh",
                  "return propertyVapour*(1-localScale) + propertyLiquid*localScale;"),
    "fp_clamp": (SRC + "fluidProperties.hh", "fpf::clamp(localDensity, _densityVapour, _densityLiquid)", 0),
    "fp_temp_ratio": (SRC + "fluidProperties.hh",
                      "return _tempLowerRatio + (latticeTemp - 0.5)*(_tempRatioDifference);"),
    "fp_verify_asserts": (SRC + "fluidProperties.hh",
                          "assert( olb::util::nearZero(_densityVapour      - rhoV  ) );"),
    "fp_write_table": (SRC + "fluidProperties.hh", "Liquid Thermal diffusivity (calculated):", 0),

    # ---------------- NS dynamics: modified Guo forcing ----------------
    "ns_class": (SRC + "modifiedGuoBGKDynamics.h", "class ForcedModifiedGuoBGKdynamics"),
    "ns_sigma_doc": (SRC + "modifiedGuoBGKDynamics.hh",
                     "related to the thermodynamic consistancy"),
    "ns_prefactor": (SRC + "modifiedGuoBGKDynamics.hh",
                     "_prefactor(_sigma / (1./_omega - 0.5) )"),
    "ns_bgk": (SRC + "modifiedGuoBGKDynamics.hh",
               "T uSqr = lbHelpers<T,DESCRIPTOR>::bgkCollision(cell, rho, u, _omega);"),
    "ns_half_force_shift": (SRC + "modifiedGuoBGKDynamics.hh", "u[iVel] += force[iVel] / (T)2.;", 2),
    "ns_sigma_shift": (SRC + "modifiedGuoBGKDynamics.hh",
                       "u[iVel] += _prefactor * (force[iVel] - extForce[iVel]) *rho / psi / psi;"),
    "ns_guo_force": (SRC + "modifiedGuoBGKDynamics.hh",
                     "lbHelpers<T,DESCRIPTOR>::addExternalForce(cell, u, _omega, rho);"),
    "ns_psi_field": (SRC + "modifiedGuoBGKDynamics.hh",
                     "const T psi   = cell.template getFieldPointer<descriptors::PSI_PSEUDO_RHO>()[0];"),

    # ---------------- thermal AD dynamics (DDF energy solver) ----------------
    "ad_class": (SRC + "pseudopotentialAdvectionDiffusionDynamics.h",
                 "class PseudopotentialAdvectionDiffusionBGKdynamics"),
    "ad_first_order_eq": (SRC + "pseudopotentialAdvectionDiffusionDynamics.hh",
                          "return lbHelpers<T, DESCRIPTOR>::equilibriumFirstOrder( iPop, rho, u );", 0),
    "ad_phi_source": (SRC + "pseudopotentialAdvectionDiffusionDynamics.hh",
                      "cell[iPop] += weight * phi;"),
    "ad_dphi_source": (SRC + "pseudopotentialAdvectionDiffusionDynamics.hh",
                       "cell[iPop] += weight * 0.5 * derivPhi ;"),
    "ad_dtv_source": (SRC + "pseudopotentialAdvectionDiffusionDynamics.hh",
                      "cdTv += descriptors::c<DESCRIPTOR>(iPop,iD) * derivTv[iD];"),
    "ad_paper_correction": (SRC + "pseudopotentialAdvectionDiffusionDynamics.hh",
                            "Perform corrections according to 10.1103 / 96.063303", 0),
    "ad_prev_store": (SRC + "pseudopotentialAdvectionDiffusionDynamics.hh",
                      "cell.template defineField<olb::descriptors::PREV_T_V>(Tv1);", 0),
    "ad_mrt_class": (SRC + "pseudopotentialAdvectionDiffusionDynamics.h",
                     "class PseudopotentialAdvectionDiffusionMRTdynamics"),
    "ad_mrt_collide": (SRC + "pseudopotentialAdvectionDiffusionDynamics.hh",
                       "T uSqr = lbHelpers<T, DESCRIPTOR>::mrtCollision(cell, temperature, u, invM_S);"),
    "ad_mrt_correction": (SRC + "pseudopotentialAdvectionDiffusionDynamics.hh",
                          "cell[iPop] += invM_correct[iPop][descriptors::shearViscIndexes<DESCRIPTOR>(iShear)]"),

    # ---------------- thermal coupling post-processor ----------------
    "tc_envelope": (SRC + "combinedShanChenThermalCouplingPostProcessor2D.hh",
                    "// include a one-cell boundary", 0),
    "tc_tpartner": (SRC + "combinedShanChenThermalCouplingPostProcessor2D.hh",
                    "tPartner = dynamic_cast<BlockLattice2D<T,descriptors::D2Q5<descriptors::VELOCITY"),
    "tc_temp_from_rho": (SRC + "combinedShanChenThermalCouplingPostProcessor2D.hh",
                         "temperature = tPartner->get(iX,iY).computeRho();"),
    "tc_psi_temp": (SRC + "combinedShanChenThermalCouplingPostProcessor2D.hh",
                    "interactionPotential(&psi,"),
    "tc_psi_store": (SRC + "combinedShanChenThermalCouplingPostProcessor2D.hh",
                     "fluidCell.template setField<descriptors::PSI_PSEUDO_RHO>(psi);"),
    "tc_sc_kernel": (SRC + "combinedShanChenThermalCouplingPostProcessor2D.hh",
                     "rhoBlockContribution[0]   += psi * blockPsi * descriptors::c<L>(iPop,0)"),
    "tc_buoyancy": (SRC + "combinedShanChenThermalCouplingPostProcessor2D.hh",
                    "T localBouyancyMagnitude = ( 1 - averageRho/localRho);"),
    "tc_force_assembled": (SRC + "combinedShanChenThermalCouplingPostProcessor2D.hh",
                           "fluidForce[iD] =  tempBouyancyForce - G*rhoBlockContribution[iD]/localRho;"),
    "tc_nabla_u": (SRC + "combinedShanChenThermalCouplingPostProcessor2D.hh",
                   "T nablaU = ( uField.get(iX-offsetX+1, iY-offsetY    , 0)"),
    "tc_dpdt": (SRC + "combinedShanChenThermalCouplingPostProcessor2D.hh",
                "interactionPotential.computeDerivativeConstRho(&dPeos, &localRho, &localTempRatio ) ;"),
    "tc_phi_compress": (SRC + "combinedShanChenThermalCouplingPostProcessor2D.hh",
                        "phi_li += localTemp * nablaU;"),
    "tc_phi_latent": (SRC + "combinedShanChenThermalCouplingPostProcessor2D.hh",
                      "phi_li -= localTempRatio * invVolumetricHeat * dPeos * nablaU;"),
    "tc_phi_store": (SRC + "combinedShanChenThermalCouplingPostProcessor2D.hh",
                     "tPartner->get(iX,iY).template setField<descriptors::PHI_THERMAL>( phi_li );"),
    "tc_velocity_coupling": (SRC + "combinedShanChenThermalCouplingPostProcessor2D.hh",
                             "uThermal[iD] =  u;"),
    "tc_generator": (SRC + "combinedShanChenThermalCouplingPostProcessor2D.hh",
                     "LatticeCouplingGenerator for advectionDiffusion coupling"),

    # ---------------- MRT thermal coupling variant ----------------
    "tcmrt_tpartner": (SRC + "combinedShanChenThermalMRTCouplingPostProcessor2D.hh",
                       "descriptors::tag::MRT,descriptors::VELOCITY, descriptors::PHI_THERMAL"),

    # ---------------- semi-hybrid thermal solver ----------------
    "sh_constant_k": (SRC + "combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh",
                      "constant_k = ( 1. / Tomega - 0.5) / descriptors::invCs2"),
    "sh_deriv_conductivity": (SRC + "combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh",
                              "derivConductivity = "),
    "sh_laplacian": (SRC + "combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh",
                     "T nablaNablaT =  tempField.get(iX-offsetX-1, iY-offsetY)"),
    "sh_grad_t": (SRC + "combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh",
                  "T nablaT =  (-tempField.get(iX-offsetX-1, iY-offsetY)"),
    "sh_grad_rho": (SRC + "combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh",
                    "T nablaRho = (-rhoField.get(iX-offsetX-1, iY-offsetY)"),
    "sh_remove_lbm_diffusion": (SRC + "combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh",
                                "phi_li -= constant_k*nablaNablaT;"),
    "sh_add_fd_diffusion": (SRC + "combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh",
                            "phi_li += invVolumetricHeat * fluidProperties.conductivity(localRho) * nablaNablaT;"),
    "sh_chain_rule": (SRC + "combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh",
                      "phi_li += invVolumetricHeat * derivConductivity * nablaRho * nablaT;"),
    "sh_comment_phase_change": (SRC + "combinedShanChenSemiHybridThermalCouplingPostProcessor2D.hh",
                                "Energy taken by phase change"),

    # ---------------- pseudo-potential force on a second (real-density) scale ----------------
    "msc_rho_scaled": (SRC + "modifiedShanChenForcedSingleComponentPostProcessor2D.hh",
                       "rhoField1.get(iX-offsetX, iY-offsetY) = cell.computeRho()*rho0[0];"),
    "msc_velocity_unscale": (SRC + "modifiedShanChenForcedSingleComponentPostProcessor2D.hh",
                             "blockU[iD] = (blockU[iD]*rho0[0]) / rhoPreCalc;"),
    "msc_force": (SRC + "modifiedShanChenForcedSingleComponentPostProcessor2D.hh",
                  "blockForce[iD] = externalBlockForce[iD] - G*rhoBlockContribution[iD]/rhoField1.get(iX-offsetX, iY-offsetY);"),
    "msc_psi_store": (SRC + "modifiedShanChenForcedSingleComponentPostProcessor2D.hh",
                      "blockCell.template setField<descriptors::PSI_PSEUDO_RHO>(psi);"),

    # ---------------- boundaries / wetting ----------------
    "bb_variable_rho_class": (SRC + "miscFeatures.h", "class BounceBackVariableRho : public BounceBack<T,DESCRIPTOR>"),
    "bb_variable_rho_doc": (SRC + "miscFeatures.h",
                            "The setDensity function is added such that the fixed density can be changed."),
    "bb_variable_rho_setter": (SRC + "miscFeatures.h", "void setDensity( T newRho ){"),
    "converter_write": (SRC + "miscFeatures.h", "Prandtl number:"),

    # ---------------- measurement / reduction functors ----------------
    "red_sum_loop": (SRC + "blockIntegralF2D.hh", "if (_indicatorF(inputTmp)) {", 0),
    "red_voxel_counter": (SRC + "blockIntegralF2D.hh", "output[_f.getTargetDim()] += voxels;"),
    "red_average_class": (SRC + "blockAverage2D.h", "class BlockAverage2D final : public customBlockSum2D<W>"),
    "red_super_average": (SRC + "superAverage2D.h", "class SuperAverage2D final : public SuperF2D<T,W>"),

    # ---------------- umbrella header + build ----------------
    "ext_umbrella_h": (SRC + "phaseChangeExtension.h",
                       "#include \"combinedShanChenSemiHybridThermalMRTCouplingPostProcessor2D.h\""),
    "ext_umbrella_hh": (SRC + "phaseChangeExtension.hh", "#include \"maxwellConstruction.hh\""),
    "src_module_mk": (SRC + "module.mk",
                      "include $(addsuffix /MakeHeader, $(current_dir))"),
    "case_root": (EX + "thermodynamicConsistency2D/definitions.mk", "ROOT            := ../../.."),
    "case_src": (EX + "thermodynamicConsistency2D/definitions.mk",
                 "SRC             := thermodynamicConsistency2D.cpp"),
    "case_module": (EX + "thermodynamicConsistency2D/module.mk",
                    "$(eval $(call sample,$(current_dir)$(OUTPUT)"),
    "case_makefile_libs": (EX + "thermodynamicConsistency2D/Makefile", "-lz -lgsl -lgslcblas"),
    "case_makefile_depend": (EX + "thermodynamicConsistency2D/Makefile", "Create dependencies for $<"),
    "case_tmpfile_absolute": (EX + "thermodynamicConsistency2D/.tmpfile", "/home/julius/Vault1/OpenLB"),

    # ---------------- examples: laplace pressure ----------------
    "ex_laplace_head": (EX + "laplace2D/laplace2D.cpp", "from which the laplace law can be validated"),
    "ex_laplace_sweep": (EX + "laplace2D/laplace2D.cpp",
                         "for(sphereRadiusFraction = 0.1; sphereRadiusFraction<=0.601;"),
    "ex_laplace_descriptor": (EX + "laplace2D/laplace2D.cpp", "#define NSDESCRIPTOR olb::descriptors::D2Q9<"),
    "ex_laplace_eval_sigma": (EX + "laplace2D/resultsEvalLaplacePressure.py",
                              "surfaceTension = pDifference * r"),
    "ex_laplace_eval_hardcoded": (EX + "laplace2D/resultsEvalLaplacePressure.py",
                                  "p_v = [ 0.000681442"),
    "ex_laplace_dynamics": (EX + "laplace2D/laplace2D.cpp",
                            "olb::ForcedModifiedGuoBGKdynamics<SimType, NSDESCRIPTOR> ModifiedGuoBulkdynamics"),

    # ---------------- examples: D2 law evaporation ----------------
    "ex_d2law_head": (EX + "d2law2D/d2law2D.cpp", "well known D2 evaporative law."),
    "ex_d2law_tc": (EX + "d2law2D/d2law2D.cpp", "const SimType TempCrit = 647;"),
    "ex_d2law_rho_liq": (EX + "d2law2D/d2law2D.cpp", "const SimType physDensityLiquid = 947.13;"),
    "ex_d2law_rho_vap": (EX + "d2law2D/d2law2D.cpp", "const SimType physDensityVapour = 0.9643;"),
    "ex_d2law_hardcoded_lattice": (EX + "d2law2D/d2law2D.cpp",
                                   "const SimType liquidDensityRatio = physDensityLiquid/8.72518;"),
    "ex_d2law_maxwell_call": (EX + "d2law2D/d2law2D.cpp",
                              "maxwellConstruction(rho, temperatureRatio); //temperatureRatio"),
    "ex_d2law_lattice_liq": (EX + "d2law2D/d2law2D.cpp", "SimType densityLatticeLiquid = rho[0];"),
    "ex_d2law_Rdimless": (EX + "d2law2D/d2law2D.cpp",
                          "SimType Rdimless = 8314.0/molarMass /(dx*dx/dt/dt) * TempCrit;"),
    "ex_d2law_converter": (EX + "d2law2D/d2law2D.cpp",
                           "olb::ThermalUnitConverter<SimType, NSDESCRIPTOR, TDESCRIPTOR> converter("),
    "ex_d2law_fluid_props": (EX + "d2law2D/d2law2D.cpp", "olb::FluidProperties<SimType> fluidProperties( "),
    "ex_d2law_conductivity_sweep": (EX + "d2law2D/d2law2D.cpp",
                                    "for( physThermalConductivity = 100.0e-3;"),
    "ex_d2law_mpi": (EX + "d2law2D/d2law2D.cpp",
                     "const int noOfCuboids = olb::singleton::mpi().getSize();"),
    "ex_d2law_periodic": (EX + "d2law2D/d2law2D.cpp", "cuboidGeometry.setPeriodicity( true, true );"),
    "ex_d2law_balancer": (EX + "d2law2D/d2law2D.cpp",
                          "HeuristicLoadBalancer<SimType> loadBalancer( cuboidGeometry );"),
    "ex_d2law_thermal_bc_decl": (EX + "d2law2D/d2law2D.cpp",
                                 "olb::sOnLatticeBoundaryCondition2D<SimType,TDESCRIPTOR> TboundaryCondition(ADlattice);"),
    "ex_d2law_thermal_bc": (EX + "d2law2D/d2law2D.cpp",
                            "olb::createAdvectionDiffusionBoundaryCondition2D<SimType,TDESCRIPTOR>(TboundaryCondition);"),
    "ex_d2law_vtk": (EX + "d2law2D/d2law2D.cpp", "olb::SuperVTMwriter2D<SimType> vtmWriterNS"),
    "ex_d2law_heatmap": (EX + "d2law2D/d2law2D.cpp", "olb::heatmap::write(planeReductionT, iT, thermalPlotParam);"),

    # ---------------- examples: nucleate boiling ----------------
    "ex_pool_head": (EX + "poolBoiling2D/poolBoiling2D.cpp", "pool boiling"),
    "ex_pool_wall_rho": (EX + "poolBoiling2D/poolBoiling2D.cpp", "const SimType wallRho = 5.0;"),
    "ex_pool_gravity_factor": (EX + "poolBoiling2D/poolBoiling2D.cpp",
                               "const SimType gravityFactor = 10000;"),
    "ex_pool_noise": (EX + "poolBoiling2D/poolBoiling2D.cpp", "const SimType noiseFraction = 0.05;"),
    "ex_pool_sigma": (EX + "poolBoiling2D/poolBoiling2D.cpp", "const SimType sigma = 0.33;"),
    "ex_pool_liquid_height": (EX + "poolBoiling2D/poolBoiling2D.cpp",
                              "const SimType initLiquidHeightFraction = 0.4;"),

    # ---------------- examples: wetting / thermodynamic consistency / others ----------------
    "ex_ww_head": (EX + "wallWettability2D/wallWettability2D.cpp",
                   "effect of wall density on the contact angle"),
    "ex_ww_wall_density": (EX + "wallWettability2D/wallWettability2D.cpp",
                           "NSlattice.defineRhoU( superGeometry, materialLowerWall, constRhoWall, zeroVelocity );"),
    "ex_ww_bb_rho": (EX + "wallWettability2D/wallWettability2D.cpp",
                     "olb::BounceBackVariableRho<SimType, NSDESCRIPTOR> bounceBackWall(rho[1]);"),
    "ex_tc_head": (EX + "thermodynamicConsistency2D/thermodynamicConsistency2D.cpp",
                   "thermodynamic consistency of the"),
    "ex_tc_tr_sweep": (EX + "thermodynamicConsistency2D/thermodynamicConsistency2D.cpp",
                       "for(temperatureRatio = 0.6; temperatureRatio<=0.851; temperatureRatio+=0.05 ){"),
    "ex_tc_sigma_sweep": (EX + "thermodynamicConsistency2D/thermodynamicConsistency2D.cpp",
                          "for( sigma = 0.33; sigma<=0.351; sigma+=0.01 ){"),
    "ex_tc_a_sweep": (EX + "thermodynamicConsistency2D/thermodynamicConsistency2D.cpp",
                      "for( eosA = 0.5/49.; eosA<=2.01/49.; eosA+=0.5/49. ){"),
    "ex_tc_folder_precision": (EX + "thermodynamicConsistency2D/thermodynamicConsistency2D.cpp",
                               "outputDirStream << std::setprecision(2);"),
    "ex_tc_eval_theory": (EX + "thermodynamicConsistency2D/resultsEvalThermoConsist2D.py",
                          "theory = np.array([ PR.getEquilibriumDensities(tr)[:2] for tr in trs ])"),
    "ex_tc_eval_pr_fixed_a": (EX + "thermodynamicConsistency2D/resultsEvalThermoConsist2D.py",
                              "PR = eos.LatticePengRobinson( acentricity = 0.3443, a=1.0/49., b=2./21. )"),
    "ex_tc_eval_sigma_list": (EX + "thermodynamicConsistency2D/resultsEvalThermoConsist2D.py",
                              "sigma_tested = ["),
    "ex_tc_eval_a_list": (EX + "thermodynamicConsistency2D/resultsEvalThermoConsist2D.py",
                          "a_tested = ", 0),
    "ex_tc_eval_trs": (EX + "thermodynamicConsistency2D/resultsEvalThermoConsist2D.py",
                       "trs = np.linspace(0.6,0.92)"),
    "ex_tc_eval_time_gate": (EX + "thermodynamicConsistency2D/resultsEvalThermoConsist2D.py",
                             "if int(time) > 5000:"),
    "ex_maxwell_head": (EX + "maxwellConstruction/maxwellConstruction.cpp",
                        "predicted densities and print them to file"),
    "ex_maxwell_writes": (EX + "maxwellConstruction/maxwellConstruction.cpp", "predictedDensities"),
    "ex_phase_sep_head": (EX + "phaseSeparation3D/phaseSeparation3D.cpp",
                          "#include \"phaseChangeExtension.hh\""),
    "ex_evap_head": (EX + "evaporatingDroplet2D/evaporatingDroplet2D.cpp",
                     "well known D2 evaporative law."),

    # ---------------- Python EOS / Maxwell (independent implementation) ----------------
    "py_maxwell": (PY + "eos.py", "def _superGetEquilibriumDensities"),
    "py_vc_from_rhoc": (PY + "eos.py", "Vc = 1/self.rhoc"),
    "py_lattice_pr": (PY + "eos.py", "class LatticePengRobinson"),
    "py_rhoc_zc3074": (PY + "eos.py", "self.rhoc = self.pc/0.3074/self.R/self.tc", 0),
    "py_real_pr": (PY + "eos.py", "class RealPengRobinson", 0),
    "py_dpdt": (PY + "eos.py", "def dPdT(self, rho, tr):"),
    "py_cs_class": (PY + "eos.py", "class CarnahanStarlingInteractionPotential"),
    "py_cs_equilibrium": (PY + "eos.py", "return self._superGetEquilibriumDensities(tr, 1.01, 5e4"),
    "py_enthalpy": (PY + "eos.py", "def enthalphyPRGong"),
    "py_self_test": (PY + "eos.py", "def testConstructionPR():", 0),
    "py_helper_findmin": (PY + "helperFunc.py", "def findMin"),
    "py_example": (PY + "ExampleMaxwellConstruction.py",
                   "pressure of the LBM Carnahan Starling EOS"),
}

# feature_ledger mapping.  ``refs`` are keys of REFS; ``files`` in the emitted
# [OSS-SOL] block are the resolved pointers of those keys.
MAPPING: list[dict] = [
    {
        "slug": "GAP-LBM-MCMP-POISEUILLE",
        "tag": "sigma-corrected-shan-chen-force",
        "method": ("力格式用 Li/Luo/Li(PRE 86, 016709) 的 σ 修正：速度移位 = F/2 + σ(F−F_ext)ρ/ψ²，"
                   "σ∈[0.3,0.35] 由参数扫描选定；单组分伪势，无第二组分、无 recoloring。"
                   "两组分分层 Poiseuille 不可直接复用，可用的是「力格式 + ψ 场缓存」的落位方式。"),
        "refs": ["ns_prefactor", "ns_sigma_shift", "ns_guo_force", "ns_psi_field",
                 "tc_sc_kernel", "tc_force_assembled"],
        "conventions": "G=-1; σ=0.3~0.35; ψ 存在 descriptor 字段 PSI_PSEUDO_RHO，每步由交互势重算",
        "transfer": "reference",
    },
    {
        "slug": "MCMP 高密度比",
        "tag": "maxwell-consistent-eos-coexistence",
        "method": ("密度比不来自互作用势强度，而来自「温度相关状态方程 + Maxwell 等面积构造」定出共存密度对："
                   "ψ=sqrt(6(p−ρ/3)/G)，p 由 Peng-Robinson/Carnahan-Starling 给出并随 T/Tc 变；"
                   "潜热通过 (∂p/∂T)_ρ 进入温度方程而不是显式 L_v。可用的是这套闭环 + 我们的密度比验收。"),
        "refs": ["eos_pr_pressure_T", "eos_psi_sqrt", "eos_pr_dpdt", "mw_operator",
                 "mw_return_densities", "ex_d2law_maxwell_call", "ex_d2law_lattice_liq",
                 "py_lattice_pr", "py_real_pr"],
        "conventions": "Tr=T/Tc=0.6~0.85; 格点 PR 用 a=2/49,b=2/21,G=-1; 共存密度由 Maxwell 构造给出",
        "transfer": "adapt",
    },
    {
        "slug": "自由面（free surface）",
        "tag": "absent-single-component-pseudopotential",
        "method": ("**没有**自由面模型：气相是被伪势方程描述的稀疏相（ρ_v>0），不是空单元；"
                   "无 ε=mass/ρ 记账、无 cell_type 位标志。界面靠密度梯度的弥散界面表达。"),
        "refs": ["tc_temp_from_rho", "ex_pool_liquid_height", "field_psi"],
        "conventions": "界面宽度由初始化噪声/界面宽度参数控制（如 liquidInterfaceWidth=8Δx）",
        "transfer": "reference",
    },
    {
        "slug": "相场 Allen-Cahn",
        "tag": "absent-no-phase-field",
        "method": "**没有**相场：单套分布函数 + 温度方程，无 φ 序参量、无 Cahn-Hilliard/Allen-Cahn、无接触角几何项。",
        "refs": ["ad_class", "field_phi", "ext_umbrella_h"],
        "conventions": "温度场亦用 DDF（D2Q5/D3Q7 加 PHI_THERMAL/PREV_T_V/PREV_PHI 三个附加字段）",
        "transfer": "reference",
    },
    {
        "slug": "GPU 融合 kernel",
        "tag": "absent-cpu-templates-only",
        "method": ("**无 GPU**：全仓是 OpenLB CPU 模板核（逐格 post-processor 双循环），无 CUDA/HIP/OpenMP 标注；"
                   "fusion 机会在 OpenLB 侧而不在本扩展。对我们只有「热-流耦合的两趟循环可融合」这个结构提示。"),
        "refs": ["tc_envelope", "tc_tpartner", "sh_laplacian"],
        "conventions": "耦合 post-processor 每次先开 (nx+2)(ny+2) 缓存场（rho/psi/u/T），再遍历内点",
        "transfer": "reference",
    },
    {
        "slug": "分布式多卡",
        "tag": "mpi-inherited-from-openlb",
        "method": ("并行完全继承 OpenLB：cuboid 几何按 MPI 进程数切分 + HeuristicLoadBalancer；"
                   "扩展自身不做 halo 交换（psi/温度场只在单块内缓存，跨块靠 OpenLB 的 SuperLattice 同步）。"),
        "refs": ["ex_d2law_mpi", "ex_d2law_balancer", "ex_d2law_periodic"],
        "conventions": "noOfCuboids = mpi size; setPeriodicity(true,true)",
        "transfer": "reference",
    },
    {
        "slug": "稀疏/多孔",
        "tag": "absent-but-wall-density-wetting",
        "method": ("无多孔介质模型；但润湿性通过「壁面密度 ρ_wall」实现（伪势的流-固作用强度），"
                   "壁面用 BounceBack(可变密度) —— 「固体=高 ρ_wall 壁」是搭多孔/绕流验证面的现成范式。"),
        "refs": ["ex_ww_head", "ex_ww_wall_density", "ex_ww_bb_rho",
                 "bb_variable_rho_class", "bb_variable_rho_setter", "ex_pool_wall_rho"],
        "conventions": "ρ_wall 越大越亲液；pairBoiling 用 ρ_wall=5.0，wallWettability 扫 ρ_wall 看接触角",
        "transfer": "adapt",
    },
    {
        "slug": "案例组织",
        "tag": "per-example-makefile-triplet",
        "method": ("每个算例一个目录，含 Makefile + definitions.mk（ROOT/SRC/OUTPUT 三个变量）+ module.mk；"
                   "参数全部硬编码在 .cpp 顶部常量区，无输入文件、无 CLI 参数。"),
        "refs": ["case_root", "case_src", "case_module", "case_makefile_libs",
                 "ex_tc_folder_precision", "ex_d2law_conductivity_sweep"],
        "conventions": "ROOT 指回 OpenLB 根；输出目录 ./results*/ + gnuplotData/data/*.dat",
        "transfer": "adapt",
    },
    {
        "slug": "测试与验证面",
        "tag": "parameter-sweep-plus-manual-pyplot",
        "method": ("**无单元测试、无 CI**。验证面 = 6 个物理算例 + 4 个 matplotlib 脚本，"
                   "脚本里直接写死仿真结果数组、理论曲线用固定 a=1/49；扫描参数与脚本查询档位不一致（见第六节）。"),
        "refs": ["ex_laplace_eval_hardcoded", "ex_tc_eval_theory", "ex_tc_eval_pr_fixed_a",
                 "ex_tc_eval_sigma_list", "ex_tc_eval_a_list", "ex_tc_eval_trs",
                 "ex_tc_eval_time_gate", "ex_laplace_eval_sigma"],
        "conventions": "判据为人眼读图；无断言、无容差、无回归基线",
        "transfer": "reference",
    },
    {
        "slug": "CI",
        "tag": "none",
        "method": "**无 CI**：无 .github/、无测试目录、无 lint；`.tmpfile` 依赖文件被误提交（含作者本机绝对路径）。",
        "refs": ["case_tmpfile_absolute", "case_makefile_depend"],
        "conventions": "构建靠手工 make；GSL 需手工加到 global.mk",
        "transfer": "reference",
    },
    {
        "slug": "NEW: 半隐式热求解器（LBM 扩散替换为显式 FD）",
        "tag": "semi-hybrid-thermal-solver",
        "method": ("把温度方程的 LBM 扩散项整体扣掉（−k·∇²T，k 由 τ_T 反算），再显式加回 "
                   "∇·(k(ρ)∇T)：k(ρ) 线性插值 + dk/dρ∇ρ·∇T 链式项。这样热扩散率可与 ν 解耦、且可随密度变 —— "
                   "对我们「变物性两相换热」是一条低成本可复用路径（代价：显式步长受扩散 CFL 限制）。"),
        "refs": ["sh_constant_k", "sh_deriv_conductivity", "sh_laplacian", "sh_grad_rho",
                 "sh_remove_lbm_diffusion", "sh_add_fd_diffusion", "sh_chain_rule",
                 "fp_interp", "fp_temp_ratio"],
        "conventions": "constant_k=(1/τ_T−0.5)/invCs2(D2Q5); T 的 DDF 用一阶平衡态",
        "transfer": "adapt",
    },
    {
        "slug": "NEW: Maxwell 等面积构造（共存密度标定器）",
        "tag": "maxwell-equal-area-gsl",
        "method": ("C++ 侧用 GSL：Brent 求根 + QAGS 自适应积分，求 p_sat 使 ∫(p−p_sat)dV=0，"
                   "输出 ρ 液/气；Python 侧独立实现同一构造（scipy），可直接当交叉校验器。"
                   "我们做高密度比 MCMP 时「给定 T 求共存密度对」正缺这一块。"),
        "refs": ["mw_operator", "mw_root_brent", "mw_qags", "mw_return_densities",
                 "mw_accuracy", "py_maxwell", "py_dpdt"],
        "conventions": "以体积 V=1/ρ 积分；accuracy 1e-6、maxIter 200、积分工作区 1000",
        "transfer": "adapt",
    },
    {
        "slug": "NEW:  thermodynamic-consistency 扫描面（Tr×σ×a）",
        "tag": "parametric-consistency-sweep",
        "method": ("把「仿真共存密度 vs Maxwell 构造解析值」做成三点扫描（Tr 0.60~0.85 × σ 0.33~0.35 × a 0.5/49~2/49），"
                   "输出 gnuplotData/data/density.dat 供后处理。可作为我们 MCMP 密度比验收的扫描模板。"),
        "refs": ["ex_tc_head", "ex_tc_tr_sweep", "ex_tc_sigma_sweep", "ex_tc_a_sweep"],
        "conventions": "目录键 a_/sigma_/tr_ 三级（setprecision(2)）；每算例只落 density.dat 末行做判读",
        "transfer": "adapt",
    },
]
