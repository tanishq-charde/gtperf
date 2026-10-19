function [cp, h, s] = gt_props(mix, t, p)
% Mixture cp (J/kg/K), absolute enthalpy (J/kg) and entropy (J/kg/K, if p is given).
cp = 0; h = 0; s = 0;
for k = 1:numel(mix.species)
    [c, hh, s0] = gt_species_props(mix.species(k), t);
    cp = cp + mix.x(k) * c;
    h = h + mix.x(k) * hh;
    s = s + mix.x(k) * s0;
end
cp = cp / mix.mw;
h = h / mix.mw;
if nargin > 2
    s = s / mix.mw - mix.R * log(p / gt_constants('P_REF'));
else
    s = NaN;
end
end
