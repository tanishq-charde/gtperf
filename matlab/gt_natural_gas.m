function fuel = gt_natural_gas()
% Typical pipeline natural gas (as gtperf.fuel.natural_gas), LHV about 47.4 MJ/kg.
fuel = gt_fuel({'CH4', 'C2H6', 'C3H8', 'CO2', 'N2'}, [0.93 0.04 0.01 0.01 0.01]);
end
