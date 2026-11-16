# FX coverage: which presets carry their whole Serum effect chain in TinyHouse

Carried: EQ (up to four bands), Delay (one), Reverb, and width (HyperDimension/Chorus/Flanger/Phaser as one width control). Not carried: Filter, Comp, Distortion, Utils, SplitMS, Bode, effects in the two bus racks, EQ in a Split branch, a second delay.

'Worth' is measured on Serum itself (calibration/dev/fx_contribution.py): the loss between the full render and one with every missing effect switched off, minus the noise floor of two Serum renders of the same preset. It is the most that carrying the missing effects could gain; over about 5 is audible in the spectrum or envelope.

## All carried (12 of 70)

Bass - INNERVERSE Reese, Bass - Reiny, Keys - BUNT, Keys - Billboard, Keys - Can't Find You, Keys - Catching Flies, Keys - Vintage Culture, Keys - Why U So, Lead - Ari, Lead - Rivo, Lead - Self Control Noise, Pluck - Scorpios

## Something missing (58 of 70), most to gain first

| preset | missing | worth | Tablor loss now |
|---|---|---:|---:|
| Bass - Let It Go | Distortion, SplitMS, Utils, EQ in Split branch x2 | 25.5 | 30.9 |
| Bass - oskar med k | Bode, Comp, Distortion, Filter, Split | 23.5 | 45.8 |
| Pad - MOBY | Comp, Filter | 22.9 | 32.0 |
| Pad - Reiny | Comp, Filter | 22.8 | 27.7 |
| Pad - BICEP | Comp, Filter | 21.7 | 23.5 |
| Pad - Aquatiq | Comp, Filter | 21.4 | 13.8 |
| Lead - Other Side | Comp, SplitMS, EQ in Split branch x2, bus-rack FX x9 | 21.3 | 24.8 |
| Bass - InDeep | Comp, Distortion, Filter | 20.7 | 13.8 |
| Keys - Zerb | Comp, Distortion, Filter | 18.2 | 21.3 |
| Lead - Space & Time | Distortion, Filter, Utils | 18.0 | 13.7 |
| Pad - Nu Aspect | Comp, Filter | 16.3 | 10.5 |
| Pluck - Nu Aspect | Comp, Distortion, Filter, SplitMS, EQ in Split branch x1 | 14.9 | 21.7 |
| Pad - MIND | Comp, Filter | 13.5 | 12.7 |
| Bass - NOTSOBAD | Bode, Distortion, Utils | 12.2 | 17.1 |
| Bass - Rivo | Comp, Distortion | 11.3 | 30.6 |
| Lead - HotLap | Bode, Comp, Distortion, Filter, 2nd delay | 10.3 | 24.4 |
| Bass - SWEDISHMF | Comp, Distortion, Filter | 10.2 | 29.7 |
| Lead - Let It Go | Comp, SplitMS, Utils, EQ in Split branch x2 | 10.0 | 15.9 |
| Pad - CamelPhat | Comp, Filter | 8.9 | 13.3 |
| Lead - Rollin | Distortion, bus-rack FX x8 | 8.4 | 11.3 |
| Bass - AVAION | Distortion | 8.3 | 21.5 |
| Lead - Can't Find You | Filter | 7.9 | 28.9 |
| Lead - Lynnic | Bode, Comp, Filter | 6.2 | 8.8 |
| Pluck - Deep In The Water | Comp, 2nd delay | 4.8 | 27.7 |
| Bass - A Classic | Comp, Distortion, SplitMS, EQ in Split branch x2 | 4.2 | 16.5 |
| Pluck - NOTSOBAD | Comp, Distortion, 2nd delay | 3.7 | 8.8 |
| Pluck - Origins | Comp, Filter | 3.6 | 17.2 |
| Lead - Sleepless | Comp, Distortion, Filter, SplitMS, EQ in Split branch x1, bus-rack FX x5 | 2.8 | 13.5 |
| Pluck - all things break | Distortion, 2nd delay | 1.7 | 14.1 |
| Keys - Loop | Distortion, Filter, bus-rack FX x2 | 1.6 | 20.6 |
| Keys - Lynnic | Distortion, Utils | 1.6 | 11.9 |
| Keys - Other Side | bus-rack FX x3 | 1.4 | 10.9 |
| Lead - The Selected | Distortion, Utils | 1.3 | 31.1 |
| Pluck - HotLap | Filter, 2nd delay | 1.2 | 16.7 |
| Pluck - London Topaz | Comp, Distortion, Filter | 1.2 | 21.6 |
| Bass - Black Out | Distortion, Filter, SplitMS, EQ in Split branch x1, bus-rack FX x5 | 1.2 | 13.8 |
| Pluck - Selected | Comp, Distortion | 1.1 | 13.3 |
| Pad - Nu Aspect 2 | Distortion, Utils, bus-rack FX x1 | 1.1 | 25.8 |
| Lead - Oskar Med | Distortion, Utils | 1.0 | 23.7 |
| Lead - Kisaya | Bode, Comp, 2nd delay | 1.0 | 9.1 |
| Bass - Gravity | Comp, Distortion, Filter, bus-rack FX x3 | 0.9 | 9.5 |
| Pad - Origins | Bode, Comp, Distortion, Filter, 2nd delay | 0.8 | 23.0 |
| Pluck - Movement | Comp, Distortion, 2nd delay | 0.8 | 14.6 |
| Lead - Loop | Distortion | 0.7 | 13.9 |
| Keys - Nyday | SplitMS, EQ in Split branch x2 | 0.6 | 8.9 |
| Lead - Self Control | Utils | 0.2 | 10.5 |
| Bass - Self Control | Distortion | 0.2 | 9.2 |
| Keys - Hans Deeper | Comp, Filter | 0.1 | 26.6 |
| Bass - BUNT | Distortion | 0.0 | 6.7 |
| Bass - The Selected | Comp, Distortion | 0.0 | 8.8 |
| Keys - Abelon | Distortion, Filter, bus-rack FX x3 | 0.0 | 11.5 |
| Keys - Atlantic | Distortion | 0.0 | 10.3 |
| Keys - Maze | Comp, Utils | 0.0 | 23.8 |
| Lead - Consoul Training | Comp | 0.0 | 16.7 |
| Lead - I'll Be Yours | Comp, Distortion | 0.0 | 10.0 |
| Lead - Nyday | Distortion, Utils, 2nd delay | 0.0 | 18.7 |
| Lead - Tonic Walter | Filter, bus-rack FX x5 | 0.0 | 17.6 |
| Pad - Small Town Kid | Comp, Distortion | 0.0 | 10.1 |
