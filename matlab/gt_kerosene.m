function fuel = gt_kerosene(c, hy, lhv, cp_liquid)
% Jet fuel as a CxHy surrogate with a specified LHV (default Jet A-1: C12H23, 43.2 MJ/kg),
% as gtperf.aero.Kerosene. Its absolute enthalpy is set so combustion releases exactly lhv.
if nargin < 1, c = 12; end
if nargin < 2, hy = 23; end
if nargin < 3, lhv = 43.2e6; end
if nargin < 4, cp_liquid = 2000; end
T = gt_constants('T_REF');
fuel.mw = (c * 12.011 + hy * 1.008) / 1000;
fuel.lhv = lhv;
fuel.reaction = [c + hy / 4, c, hy / 2];
fuel.inert_names = {};
fuel.inert_x = [];
ref = gt_mixture({'O2', 'CO2', 'H2O'}, [1 1 1]);
[~, h_o2] = gt_species_props(ref.species(1), T);
[~, h_co2] = gt_species_props(ref.species(2), T);
[~, h_h2o] = gt_species_props(ref.species(3), T);
h_ref = (fuel.reaction(2) * h_co2 + fuel.reaction(3) * h_h2o - fuel.reaction(1) * h_o2) / fuel.mw + lhv;
fuel.q = @(t_fuel, eta) h_ref + cp_liquid * (t_fuel - T) - (1 - eta) * lhv;
end
