function t_out = gt_change_pressure(st, p_out, eta, polytropic, compress)
% Exit temperature after compression or expansion with an isentropic or polytropic efficiency.
% A polytropic process is integrated in 50 small isentropic steps, as in gtperf.components.
if ~polytropic
    t_out = step(st.gas, st.t, st.p, p_out, eta, compress);
    return
end
n = 50;
ratio = (p_out / st.p) ^ (1 / n);
t = st.t; p = st.p;
for k = 1:n
    t = step(st.gas, t, p, p * ratio, eta, compress);
    p = p * ratio;
end
t_out = t;
end

function t2 = step(gas, t, p1, p2, eta, compress)
[~, h1, s1] = gt_props(gas, t, p1);
t_s = gt_t_from_s(gas, s1, p2, t * (p2 / p1) ^ 0.28);
[~, h_s] = gt_props(gas, t_s);
if compress
    dh = (h_s - h1) / eta;
else
    dh = (h_s - h1) * eta;
end
t2 = gt_t_from_h(gas, h1 + dh, t);
end
