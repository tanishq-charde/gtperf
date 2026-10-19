function [out, fuel_flow] = gt_combustor(st, fuel, t_out, dp_frac, eta, t_fuel)
% Burn fuel to reach exit temperature t_out. Returns the exit state and fuel flow (kg/s).
if nargin < 4, dp_frac = 0.04; end
if nargin < 5, eta = 0.995; end
if nargin < 6, t_fuel = 288.15; end
q_fuel = fuel.q(t_fuel, eta);
[~, h_in] = gt_props(st.gas, st.t);
residual = @(far) h_in + far * q_fuel - (1 + far) * prod_h(fuel, st.gas, far, t_out);
far_max = 0.999 * gt_stoich_far(fuel, st.gas);
opts = optimset('TolX', 1e-16);
far = fzero(residual, [0 far_max], opts);
out = gt_state(st.w * (1 + far), t_out, st.p * (1 - dp_frac), gt_products(fuel, st.gas, far));
fuel_flow = st.w * far;
end

function h = prod_h(fuel, air, far, t)
[~, h] = gt_props(gt_products(fuel, air, far), t);
end
