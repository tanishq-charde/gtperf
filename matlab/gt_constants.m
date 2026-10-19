function v = gt_constants(name)
% Physical constants shared by the MATLAB port (same values as gtperf.gas).
switch name
    case 'R'
        v = 8.314462618;   % J/(mol K)
    case 'P_REF'
        v = 101325.0;      % Pa
    case 'T_REF'
        v = 298.15;        % K
    otherwise
        error('gt_constants:name', 'Unknown constant %s', name);
end
end
