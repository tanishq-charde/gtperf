function fuel = gt_fuel(names, x)
% Gaseous hydrocarbon fuel burned to completion. LHV comes from the species enthalpies.
fuel.mix = gt_mixture(names, x);
fuel.mw = fuel.mix.mw;
o2 = 0; co2 = 0; h2o = 0;
for k = 1:numel(fuel.mix.species)
    sp = fuel.mix.species(k);
    if any(strcmp(sp.name, {'CH4', 'C2H6', 'C3H8'}))
        c = sp.atoms(1); hy = sp.atoms(2);
        o2 = o2 + fuel.mix.x(k) * (c + hy / 4);
        co2 = co2 + fuel.mix.x(k) * c;
        h2o = h2o + fuel.mix.x(k) * hy / 2;
    end
end
fuel.reaction = [o2 co2 h2o];   % mol per mol fuel: O2 used, CO2 and H2O formed
T = gt_constants('T_REF');
ref = gt_mixture({'O2', 'CO2', 'H2O'}, [1 1 1]);
[~, h_o2] = gt_species_props(ref.species(1), T);
[~, h_co2] = gt_species_props(ref.species(2), T);
[~, h_h2o] = gt_species_props(ref.species(3), T);
[~, h_fuel] = gt_props(fuel.mix, T);
fuel.lhv = (h_fuel * fuel.mw + o2 * h_o2 - co2 * h_co2 - h2o * h_h2o) / fuel.mw;
inert = ~ismember(fuel.mix.names, {'CH4', 'C2H6', 'C3H8'});
fuel.inert_names = fuel.mix.names(inert);
fuel.inert_x = fuel.mix.x(inert);
% energy brought in per kg fuel for the combustor balance: absolute enthalpy less unburned chemical energy
fuel.q = @(t_fuel, eta) fuel_h(fuel.mix, t_fuel) - (1 - eta) * fuel.lhv;
end

function h = fuel_h(mix, t)
[~, h] = gt_props(mix, t);
end
