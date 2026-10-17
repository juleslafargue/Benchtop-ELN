# NMRShiftDB2 data

`nmr_shifts.sqlite` is a database derived from **NMRShiftDB2** (https://nmrshiftdb.nmr.uni-koeln.de),
the open NMR database of the University of Cologne. It holds only aggregated shift statistics
(count, mean, spread) per atom environment; no structures or spectra are included.

- Source data: NMRShiftDB2 export `nmrshiftdb2withsignals.sd`
  (https://sourceforge.net/projects/nmrshiftdb2/files/data/). The table shipped with Benchtop was
  built from the 13C extract published with Rull, Fischer & Kuhn, "NMR shift prediction from small
  data quantities", J. Cheminform. 2023, 15, 114 (https://doi.org/10.1186/s13321-023-00785-x).
  `./benchtop update-nmr-data` rebuilds it on your computer from the current full export, which
  adds 1H data.
- Licence: the **nmrshiftdb2 Data License**,
  https://nmrshiftdb.nmr.uni-koeln.de/nmrshiftdbhtml/nmrshiftdb2datalicense.txt. In summary (the
  linked text governs): use, including commercial use, is permitted; nmrshiftdb2 must be credited;
  derived databases such as this file must be offered under the same licence; software that uses
  the data for prediction must be under an OSI-approved licence (Benchtop is Apache-2.0).
- Credit: Kuhn, S.; Schlörer, N. E. "Facilitating quality control for spectra assignments of small
  organic molecules: nmrshiftdb2 – a free in-house NMR database with integrated LIMS for academic
  service laboratories." Magn. Reson. Chem. 2015, 53, 582–589.
