function mix = gt_humid_air(t, p, rh)
% Humid air for temperature t (K), pressure p (Pa) and relative humidity rh (0..1).
tc = t - 273.15;
psat = 611.21 * exp((18.678 - tc / 234.5) * (tc / (257.14 + tc)));   % Buck (1996)
x_h2o = rh * psat / p;
dry = [0.78084 0.20948 0.00934 0.00034];
dry = dry / sum(dry) * (1 - x_h2o);
mix = gt_mixture({'N2', 'O2', 'Ar', 'CO2', 'H2O'}, [dry x_h2o]);
end
