function far = gt_stoich_far(fuel, air)
% Stoichiometric fuel-air ratio by mass.
o2_frac = air.x(strcmp(air.names, 'O2'));
far = fuel.mw / (fuel.reaction(1) / o2_frac * air.mw);
end
