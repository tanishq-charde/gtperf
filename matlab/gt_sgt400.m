function c = gt_sgt400(tit, turbine_eff)
% Cycle definition of the SGT-400 13 MW calibration (assumptions as examples/sgt400_13mw.toml).
% tit and turbine_eff are the values fitted by gtperf calibrate.
c.pressure_ratio = 17.1;
c.compressor_eff = 0.89;
c.fuel = gt_natural_gas();
c.tit = tit;
c.combustor_dp = 0.04;
c.combustor_eff = 0.995;
c.turbine_eff = turbine_eff;
c.cooling = struct('ngv', 0.07, 'rotor', 0.05, 'pt', 0.03, 'port', 0.6);
c.mechanical_efficiency = 0.99;
c.generator_efficiency = 0.98;
end
