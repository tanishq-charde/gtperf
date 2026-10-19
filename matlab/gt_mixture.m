function mix = gt_mixture(names, x)
% Ideal-gas mixture of fixed composition. names: cell array of species names,
% x: mole fractions (normalised here). Returns a struct used by gt_cp, gt_h, gt_s.
S = gt_species_data();
all_names = {S.name};
x = x(:)' / sum(x);
keep = x > 0;
names = names(keep);
x = x(keep);
idx = zeros(1, numel(names));
for k = 1:numel(names)
    i = find(strcmp(all_names, names{k}));
    if isempty(i)
        error('gt_mixture:species', 'Unknown species %s', names{k});
    end
    idx(k) = i;
end
mix.names = names;
mix.x = x;
mix.species = S(idx);
mix.mw = sum([mix.species.mw] .* x);
mix.R = gt_constants('R') / mix.mw;
end
