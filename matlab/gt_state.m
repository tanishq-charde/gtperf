function st = gt_state(w, t, p, gas)
% Flow state: mass flow (kg/s), total temperature (K), total pressure (Pa) and gas mixture.
st = struct('w', w, 't', t, 'p', p, 'gas', gas);
end
