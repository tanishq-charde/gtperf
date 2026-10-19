function jet = gt_nozzle(st, p_amb, eta)
% Convergent nozzle: exit static state, velocity, area and gross thrust (as gtperf.aero.nozzle).
% eta applies to the enthalpy drop. The nozzle chokes when the exit would be supersonic.
if nargin < 3, eta = 1; end
[~, h0, s0] = gt_props(st.gas, st.t, st.p);
mach_m1 = @(p) exit_mach(st, h0, s0, p, eta) - 1;
p_exit = p_amb;
jet.choked = false;
if p_amb < st.p && mach_m1(p_amb) > 0
    p_exit = fzero(mach_m1, [p_amb, st.p * 0.999], optimset('TolX', 1e-6));
    jet.choked = true;
end
[~, t, v] = exit_mach(st, h0, s0, p_exit, eta);
rho = p_exit / (st.gas.R * t);
jet.w = st.w; jet.v = v; jet.t = t; jet.p = p_exit;
jet.area = st.w / (rho * v);
jet.gross_thrust = st.w * v + jet.area * (p_exit - p_amb);
end

function [m, t, v] = exit_mach(st, h0, s0, p, eta)
t_is = gt_t_from_s(st.gas, s0, p, st.t * (p / st.p) ^ 0.25);
[~, h_is] = gt_props(st.gas, t_is);
h = h0 - eta * (h0 - h_is);
t = gt_t_from_h(st.gas, h, t_is);
v = sqrt(max(2 * (h0 - h), 0));
[cp] = gt_props(st.gas, t);
gamma = cp / (cp - st.gas.R);
m = v / sqrt(gamma * st.gas.R * t);
end
