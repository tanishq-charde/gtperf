function [t, p] = gt_isa(altitude_m, dt)
% ISA static temperature (K) and pressure (Pa) up to 20 km, with temperature offset dt.
if nargin < 2, dt = 0; end
if altitude_m <= 11000
    t = 288.15 - 0.0065 * altitude_m;
    p = 101325 * (t / 288.15) ^ 5.25588;
else
    t = 216.65;
    p = 22632.06 * exp(-(altitude_m - 11000) / 6341.62);
end
t = t + dt;
end
