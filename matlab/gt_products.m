function prod = gt_products(fuel, air, far)
% Products of burning far kg fuel per kg air to completion.
names = air.names;
n = air.x / air.mw;
n_fuel = far / fuel.mw;
for k = 1:numel(fuel.inert_names)
    [names, n] = add_moles(names, n, fuel.inert_names{k}, fuel.inert_x(k) * n_fuel);
end
[names, n] = add_moles(names, n, 'O2', -fuel.reaction(1) * n_fuel);
[names, n] = add_moles(names, n, 'CO2', fuel.reaction(2) * n_fuel);
[names, n] = add_moles(names, n, 'H2O', fuel.reaction(3) * n_fuel);
if n(strcmp(names, 'O2')) < 0
    error('gt_products:rich', 'Fuel-air ratio %.4f is richer than stoichiometric', far);
end
prod = gt_mixture(names, n);
end

function [names, n] = add_moles(names, n, name, dn)
i = find(strcmp(names, name));
if isempty(i)
    names{end + 1} = name;
    n(end + 1) = dn;
else
    n(i) = n(i) + dn;
end
end
