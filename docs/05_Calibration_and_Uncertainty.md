# 5. Conversion, calibration and uncertainty

## Why Isc

The short-circuit current is proportional to the number of photons absorbed by the cells, weighted by the spectral response of silicon (~300–1100 nm). That is exactly what the cover changes, through transmission, reflection and scattering. Isc barely depends on temperature (~+0.05 % per kelvin). The power at the maximum power point, on the other hand, mixes the optical part with temperature, voltage and internal losses.

## Conversion

With `k = Vrefint / r` (mV per count; ~0.806 mV at 3.3 V):

```
I_n [mA]   = (a_n − zero_n) · k / R_shunt,n / cal_Isc,n
Voc_n [mV] = v_n · k · divider_n − cal_Voc,n
```

- `a_n`, `v_n`, `r` are the averages sent by the firmware, in counts.
- `zero_n` is the shunt reading in the last Voc window (no current).
- `cal_Isc,n` and `cal_Voc,n` come from the module calibration (1 and 0 without calibration).

With *ADC supply from Vrefint* turned off, `k = Vref / 4096`.

## Uncertainty of the relative Isc

The ratio Isc_n / Isc_ref is the main quantity. Things that do **not** affect it:

- changes in sunlight, because the modules are sampled together;
- the ADC reference voltage (it appears above and below and cancels);
- 60 Hz interference, which cancels in the synchronized averages;
- the channel offsets, subtracted every minute.

What **remains**:

| Source | Typical size | How to reduce it |
|---|---|---|
| ADC noise | ~0.01 mA in 1 s (σ of 2 counts / √6000); ~0.003 % of the ratio with a 1 min average | Longer averaging window |
| Shunt tolerance | up to 1 % per shunt | Measure each shunt and enter it; or calibrate the modules |
| Differences between "identical" modules | 1–2 % | **Calibrate the modules** |
| Thermal drift of the shunts | 50 ppm/K: 0.05 % with a 10 K difference | Shunts in the shade, close to each other |
| Current lost in the module parallel resistance | V/Rp: ~0.25 V / 500 Ω = 0.5 mA (0.4 %), almost equal in all 4 | Already largely cancels in the ratio |
| Non-uniform light, partial shading | any | Modules side by side, same orientation, no shadows |

## Module calibration

Modules from the same batch differ by ~1–2 % in Isc and by a few mV in Voc. Shunts and wiring also differ slightly. Since the experiment compares covers through the ratio between modules, these differences would show up as if they were an effect of the cover.

Procedure:

1. Put the four modules under the **same condition**: all without a cover, or all with the reference cover. Check that there is no shadow or different dirt.
2. Measure for a few minutes, preferably with steady sun. Use an **Average** of 5 min or more. At least one Voc measurement must fall within this period.
3. Click **Calibrate modules…**, check the values (ratios close to 1 and Voc differences of a few mV) and apply.
4. Put the covers on and measure.

Repeat the calibration from time to time and whenever a module or shunt is replaced. A good practice is to interleave: calibration, covers, calibration again. If the two calibrations agree within the error, the system was stable during the measurement. Another option is to swap the covers between modules from one run to the next: if the ratio follows the cover rather than the module, the effect comes from the cover.

## ΔT estimate from Voc

Voc drops by ~2.2 mV per kelvin in each silicon cell. With Ns cells in series, a temperature difference ΔT between two modules gives ΔVoc ≈ Ns · (−2.2 mV/K) · ΔT.

Voc also rises with light: ΔVoc_light = Ns · n · (kT/q) · ln(Isc_n / Isc_ref). The interface removes that part using the mean Isc of the previous 10 s:

```
ΔT_n = (ΔVoc_n − Ns · n · (kT/q) · ln(Isc_n / Isc_ref)) / (Ns · dVoc/dT)
```

Parameters in *Configure modules…*: Ns (default 4), ideality factor n (1.3), typical module temperature for kT/q (45 °C) and dVoc/dT per cell (−2.2 mV/K).

**Limitations:** ΔT is a **relative estimate**, useful to see whether one cover heats its module more than another. Its error comes from:

- n and dVoc/dT, which depend on the cell: ±20 % on the value of ΔT;
- the Voc calibration, which must be done with the modules at the same temperature;
- the resolution: 1 mV of ΔVoc ≈ 0.1 K.

For absolute temperature, glue a thermocouple or an NTC to the back of each module. Inputs PA0 and PB1 are free.
