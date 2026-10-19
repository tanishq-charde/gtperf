function [cp, h, s0] = gt_species_props(sp, t)
% Molar cp (J/mol/K), absolute enthalpy (J/mol) and standard entropy (J/mol/K) of one species.
if t > sp.tmid
    a = sp.high;
else
    a = sp.low;
end
R = gt_constants('R');
cp = R * (a(1) + t * (a(2) + t * (a(3) + t * (a(4) + t * a(5)))));
h = R * t * (a(1) + t * (a(2) / 2 + t * (a(3) / 3 + t * (a(4) / 4 + t * a(5) / 5))) + a(6) / t);
s0 = R * (a(1) * log(t) + t * (a(2) + t * (a(3) / 2 + t * (a(4) / 3 + t * a(5) / 4))) + a(7));
end
