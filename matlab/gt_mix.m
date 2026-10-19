function out = gt_mix(a, b)
% Adiabatic mixing of two streams at the pressure of stream a.
if b.w == 0, out = a; return; end
if a.w == 0, out = b; return; end
names = a.gas.names;
n = a.gas.x * a.w / a.gas.mw;
nb = b.gas.x * b.w / b.gas.mw;
for k = 1:numel(b.gas.names)
    i = find(strcmp(names, b.gas.names{k}));
    if isempty(i)
        names{end + 1} = b.gas.names{k};
        n(end + 1) = nb(k);
    else
        n(i) = n(i) + nb(k);
    end
end
gas = gt_mixture(names, n);
[~, ha] = gt_props(a.gas, a.t);
[~, hb] = gt_props(b.gas, b.t);
w = a.w + b.w;
out = gt_state(w, gt_t_from_h(gas, (a.w * ha + b.w * hb) / w, a.t), a.p, gas);
end
