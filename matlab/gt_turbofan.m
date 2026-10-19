function r = gt_turbofan(c, w, flight)
% Two-spool separate-exhaust turbofan at a design point (port of gtperf.aero.Turbofan).
%
% c: cycle struct, any field left out takes the default below. Required: bpr, fan_pr,
%    hpc_pr, tet (K). Compressor efficiencies are polytropic, turbine efficiencies isentropic.
% w: total intake mass flow (kg/s).
% flight: struct with altitude_m, mach and optionally isa_dt (K) and rh (0..1, default dry).
% Returns stations (s0 s2 s13 s21 s25 s3 s4 s41 s45 s48 s5 s8 s18), jets and performance.
d = struct('fan_core_pr', [], 'fan_eff', 0.91, 'booster_eff', 0.90, 'hpc_eff', 0.90, 'hpt_eff', 0.89, ...
           'lpt_eff', 0.91, 'intake_recovery', 0.995, 'bypass_duct_dp', 0.015, 'core_duct_dp', 0.01, ...
           'combustor_dp', 0.04, 'combustor_eff', 0.999, 'turbine_duct_dp', 0.01, 'jet_pipe_dp', 0.01, ...
           'cooling_fraction', 0.10, 'hp_offtake', 0, 'customer_bleed', 0, 'mechanical_efficiency', 0.99, ...
           'nozzle_efficiency', 1.0, 'fuel', []);
f = fieldnames(d);
for k = 1:numel(f)
    if ~isfield(c, f{k}), c.(f{k}) = d.(f{k}); end
end
if isempty(c.fuel), c.fuel = gt_kerosene(); end
if ~isfield(flight, 'isa_dt'), flight.isa_dt = 0; end
if ~isfield(flight, 'rh'), flight.rh = 0; end

% free stream and intake
[t_amb, p_amb] = gt_isa(flight.altitude_m, flight.isa_dt);
air = gt_humid_air(t_amb, p_amb, flight.rh);
[cp, h_amb, s_amb] = gt_props(air, t_amb, p_amb);
v0 = flight.mach * sqrt(cp / (cp - air.R) * air.R * t_amb);
t0 = gt_t_from_h(air, h_amb + 0.5 * v0^2, t_amb);
[~, ~, s_t0] = gt_props(air, t0, p_amb);
s0 = gt_state(w, t0, p_amb * exp((s_t0 - s_amb) / air.R), air);
s2 = duct(s0, 1 - c.intake_recovery);

% fan, booster and HP compressor
w_core = w / (1 + c.bpr);
s13 = s2; s13.w = w - w_core;
[s13, fan_bypass] = gt_compressor(s13, c.fan_pr, c.fan_eff, true);
core_pr = c.fan_core_pr;
core_eff = c.booster_eff;
if isempty(core_pr) || core_pr == c.fan_pr
    core_pr = c.fan_pr;
    core_eff = c.fan_eff;
end
s21 = s2; s21.w = w_core;
[s21, fan_core] = gt_compressor(s21, core_pr, core_eff, true);
s25 = duct(s21, c.core_duct_dp);
[s3, hpc_power] = gt_compressor(s25, c.hpc_pr, c.hpc_eff, true);

% combustor and cooling air
bleed = s3; bleed.w = s3.w * c.cooling_fraction;
burner_in = s3; burner_in.w = s3.w * (1 - c.cooling_fraction - c.customer_bleed);
[s4, fuel_flow] = gt_combustor(burner_in, c.fuel, c.tet, c.combustor_dp, c.combustor_eff);
s41 = gt_mix(s4, bleed);

% turbines from the spool power balances
fan_power = fan_bypass + fan_core;
s45 = expand(s41, c.hpt_eff, (hpc_power + c.hp_offtake) / c.mechanical_efficiency);
s48 = duct(s45, c.turbine_duct_dp);
s5 = expand(s48, c.lpt_eff, fan_power / c.mechanical_efficiency);
s8 = duct(s5, c.jet_pipe_dp);
s18 = duct(s13, c.bypass_duct_dp);
if s8.p <= p_amb
    error('gt_turbofan:lpt', 'Core exhaust total pressure is below ambient: the LPT cannot drive this fan');
end

r.stations = struct('s0', s0, 's2', s2, 's13', s13, 's21', s21, 's25', s25, 's3', s3, 's4', s4, ...
                    's41', s41, 's45', s45, 's48', s48, 's5', s5, 's8', s8, 's18', s18);
r.core_jet = gt_nozzle(s8, p_amb, c.nozzle_efficiency);
r.bypass_jet = gt_nozzle(s18, p_amb, c.nozzle_efficiency);
r.v0 = v0;
r.fuel_flow = fuel_flow;
r.net_thrust = r.core_jet.gross_thrust + r.bypass_jet.gross_thrust - w * v0;
r.specific_thrust = r.net_thrust / w;
r.tsfc = fuel_flow / r.net_thrust;   % kg/(N s)
end

function out = duct(st, dp_frac)
out = st;
out.p = st.p * (1 - dp_frac);
end

function out = expand(st, eta, work)
% Turbine exit state that delivers the required work (W).
residual = @(p) turbine_work(st, p, eta) - work;
p = fzero(residual, [st.p * 1e-3, st.p * (1 - 1e-12)], optimset('TolX', 1e-6));
out = gt_turbine(st, p, eta);
end

function w = turbine_work(st, p, eta)
[~, w] = gt_turbine(st, p, eta);
end
