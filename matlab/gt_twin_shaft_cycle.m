function r = gt_twin_shaft_cycle(c, air_flow, ambient)
% Twin-shaft industrial gas turbine at a design point (port of gtperf.cycle.TwinShaftCycle).
%
% c: struct with fields pressure_ratio, compressor_eff (polytropic), fuel, tit (K),
%    combustor_dp, combustor_eff, turbine_eff (isentropic, both turbines),
%    cooling (struct with ngv, rotor, pt, port as gtperf.cycle.Cooling) or the legacy
%    cooling_fraction (all NGV air), mechanical_efficiency, generator_efficiency,
%    and optionally inlet_loss and exhaust_loss (Pa).
% ambient: struct with t (K), p (Pa), rh (0..1).
% Returns a struct of stations (s0, s2, s3, s31, s4, s41, s44, s45, s5) and performance.
if ~isfield(c, 'inlet_loss'), c.inlet_loss = 0; end
if ~isfield(c, 'exhaust_loss'), c.exhaust_loss = 0; end
if ~isfield(c, 'cooling'), c.cooling = struct('ngv', c.cooling_fraction, 'rotor', 0, 'pt', 0, 'port', 0.6); end
k = c.cooling;
s0 = gt_state(air_flow, ambient.t, ambient.p, gt_humid_air(ambient.t, ambient.p, ambient.rh));
s2 = s0; s2.p = s0.p - c.inlet_loss;
[s3, comp_power] = gt_compressor(s2, c.pressure_ratio, c.compressor_eff, true);
ngv = s3; ngv.w = s2.w * k.ngv;
rotor = s3; rotor.w = s2.w * k.rotor;
pt_air = s3; pt_air.w = 0;
if k.pt > 0
    % interstage air leaves at the port, compressed only that far (exact for a polytropic efficiency)
    port = gt_compressor(s2, c.pressure_ratio ^ k.port, c.compressor_eff, true);
    pt_air = port; pt_air.w = s2.w * k.pt;
    [~, h3] = gt_props(s3.gas, s3.t);
    [~, h_port] = gt_props(port.gas, port.t);
    comp_power = comp_power - pt_air.w * (h3 - h_port);
end
s31 = s3; s31.w = s2.w * (1 - k.ngv - k.rotor - k.pt);
[s4, fuel_flow] = gt_combustor(s31, c.fuel, c.tit, c.combustor_dp, c.combustor_eff);
s41 = gt_mix(s4, ngv);

p5 = ambient.p + c.exhaust_loss;
needed = comp_power / c.mechanical_efficiency;
p45 = fzero(@(p) hpt_power(s41, p, c.turbine_eff) - needed, [p5 * 1.0001, s41.p * 0.9999], ...
            optimset('TolX', 1e-9));
[s44, hpt] = gt_turbine(s41, p45, c.turbine_eff);
s45 = gt_mix(gt_mix(s44, rotor), pt_air);
[s5, pt] = gt_turbine(s45, p5, c.turbine_eff);

r.stations = struct('s0', s0, 's2', s2, 's3', s3, 's31', s31, 's4', s4, 's41', s41, 's44', s44, ...
                    's45', s45, 's5', s5);
r.fuel_flow = fuel_flow;
r.compressor_power = comp_power;
r.turbine_power = hpt + pt;
r.shaft_power = pt * c.mechanical_efficiency;
r.electrical_power = r.shaft_power * c.generator_efficiency;
r.thermal_efficiency = r.electrical_power / (fuel_flow * c.fuel.lhv);
r.heat_rate = 3600 / r.thermal_efficiency;   % kJ/kWh
end

function w = hpt_power(st, p, eta)
[~, w] = gt_turbine(st, p, eta);
end
