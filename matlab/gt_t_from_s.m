function t = gt_t_from_s(mix, s, p, t)
% Temperature at which the mixture has entropy s at pressure p (damped Newton from guess t).
for it = 1:50
    [cp, ~, ss] = gt_props(mix, t, p);
    dt = (s - ss) * t / cp;
    t = max(t + dt, 0.5 * t);
    if abs(dt) < 1e-9
        return
    end
end
error('gt_t_from_s:converge', 'gt_t_from_s did not converge');
end
