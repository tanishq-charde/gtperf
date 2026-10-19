function t = gt_t_from_h(mix, h, t)
% Temperature at which the mixture has absolute enthalpy h (damped Newton from guess t).
for it = 1:50
    [cp, hh] = gt_props(mix, t);
    dt = (h - hh) / cp;
    t = max(t + dt, 0.5 * t);
    if abs(dt) < 1e-9
        return
    end
end
error('gt_t_from_h:converge', 'gt_t_from_h did not converge');
end
