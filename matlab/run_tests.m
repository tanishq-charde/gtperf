function run_tests()
% Compare the MATLAB port with reference values from the Python package (reference_values.csv).
% Works in MATLAB and GNU Octave. Errors if any check fails.
here = fileparts(mfilename('fullpath'));
fid = fopen(fullfile(here, 'reference_values.csv'));
fgetl(fid);
ref = struct();
while true
    line = fgetl(fid);
    if ~ischar(line), break; end
    parts = strsplit(line, ',');
    ref.(parts{1}) = str2double(parts{2});
end
fclose(fid);

failures = 0;
air = gt_humid_air(288.15, 101325, 0.6);
for t = [250 800 1500]
    [cp, h, s] = gt_props(air, t, 5e5);
    failures = failures + check(sprintf('air cp %d K', t), cp, ref.(sprintf('air_cp_%d', t)), 1e-12);
    failures = failures + check(sprintf('air h %d K', t), h, ref.(sprintf('air_h_%d', t)), 1e-12);
    failures = failures + check(sprintf('air s %d K', t), s, ref.(sprintf('air_s_%d', t)), 1e-12);
end
ng = gt_natural_gas();
failures = failures + check('natural gas LHV', ng.lhv, ref.ng_lhv, 1e-12);
failures = failures + check('stoichiometric far', gt_stoich_far(ng, air), ref.ng_stoich_far, 1e-12);

c = gt_sgt400(ref.sgt_tit, ref.sgt_turbine_eff);
cases = {'iso', struct('t', 288.15, 'p', 101325, 'rh', 0.6); 'hot', struct('t', 308.15, 'p', 101325, 'rh', 0.3)};
for k = 1:size(cases, 1)
    name = cases{k, 1};
    r = gt_twin_shaft_cycle(c, ref.sgt_air_flow, cases{k, 2});
    f = @(q) ref.(sprintf('sgt_%s_%s', name, q));
    failures = failures + check([name ' power'], r.electrical_power, f('power'), 1e-9);
    failures = failures + check([name ' heat rate'], r.heat_rate, f('heat_rate'), 1e-9);
    failures = failures + check([name ' fuel flow'], r.fuel_flow, f('fuel_flow'), 1e-9);
    failures = failures + check([name ' T3'], r.stations.s3.t, f('t3'), 1e-9);
    failures = failures + check([name ' T41'], r.stations.s41.t, f('t41'), 1e-9);
    failures = failures + check([name ' P45'], r.stations.s45.p, f('p45'), 1e-9);
    failures = failures + check([name ' T5'], r.stations.s5.t, f('t5'), 1e-9);
end

fan = struct('bpr', 10, 'fan_pr', 1.5, 'fan_core_pr', 2.4, 'hpc_pr', 17, 'tet', 1600, 'fan_eff', 0.91, ...
             'booster_eff', 0.90, 'hpc_eff', 0.90, 'hpt_eff', 0.89, 'lpt_eff', 0.91, 'intake_recovery', 0.995, ...
             'combustor_dp', 0.04, 'cooling_fraction', 0.10, 'hp_offtake', 150e3);   % examples/turbofan_cruise.toml
r = gt_turbofan(fan, 170, struct('altitude_m', 10668, 'mach', 0.78));
s = r.stations;
checks = {'net thrust', r.net_thrust, ref.fan_net_thrust; 'TSFC', r.tsfc, ref.fan_tsfc; ...
          'fuel flow', r.fuel_flow, ref.fan_fuel_flow; 'flight speed', r.v0, ref.fan_v0; ...
          'T3', s.s3.t, ref.fan_t3; 'P3', s.s3.p, ref.fan_p3; 'T41', s.s41.t, ref.fan_t41; ...
          'P45', s.s45.p, ref.fan_p45; 'P5', s.s5.p, ref.fan_p5; 'T5', s.s5.t, ref.fan_t5; ...
          'core jet velocity', r.core_jet.v, ref.fan_core_jet_v; 'bypass jet velocity', r.bypass_jet.v, ...
          ref.fan_bypass_jet_v};
for k = 1:size(checks, 1)
    failures = failures + check(['turbofan ' checks{k, 1}], checks{k, 2}, checks{k, 3}, 1e-8);
end

if failures > 0
    error('run_tests:failed', '%d check(s) failed', failures);
end
fprintf('All MATLAB checks passed\n');
end

function failed = check(label, value, expected, rtol)
err = abs(value / expected - 1);
failed = err > rtol;
status = 'ok';
if failed, status = 'FAIL'; end
fprintf('%-24s %22.12g %22.12g  rel err %8.1e  %s\n', label, value, expected, err, status);
end
