function [out, power] = gt_compressor(st, pressure_ratio, eta, polytropic)
% Compressor: exit state and absorbed power (W). Efficiency is polytropic unless polytropic = false.
if nargin < 4, polytropic = true; end
p_out = st.p * pressure_ratio;
out = st;
out.t = gt_change_pressure(st, p_out, eta, polytropic, true);
out.p = p_out;
[~, h_in] = gt_props(st.gas, st.t);
[~, h_out] = gt_props(out.gas, out.t);
power = st.w * (h_out - h_in);
end
