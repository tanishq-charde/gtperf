function [out, power] = gt_turbine(st, p_out, eta, polytropic)
% Turbine: exit state at p_out and delivered power (W). Efficiency is isentropic unless polytropic = true.
if nargin < 4, polytropic = false; end
out = st;
out.t = gt_change_pressure(st, p_out, eta, polytropic, false);
out.p = p_out;
[~, h_in] = gt_props(st.gas, st.t);
[~, h_out] = gt_props(out.gas, out.t);
power = st.w * (h_in - h_out);
end
