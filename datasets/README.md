# ECHO public datasets

These datasets are for offline ML engineering and research validation. They should not be treated as interchangeable clinical labels.

## Sources

| Dataset | Sound focus | Suggested use | Source |
| --- | --- | --- | --- |
| Coswara | breathing, cough, speech, metadata | respiratory-sound representation and subject-level splits | [IISc Coswara-Data](https://github.com/iiscleap/Coswara-Data) |
| COUGHVID | crowdsourced cough | cough quality and cough-related label experiments | [COUGHVID Signal Classification](https://github.com/pmastrogiovanni/Coughvid_Signal_Classification) |
| ICBHI 2017 | clinical lung sounds, wheezes, crackles | lung-sound event classification | [ICBHI mirror](https://github.com/Ashindustry007/Vocal-Biomarker-ICBHI-final-database) |

Use the real downloader from the project root:

```powershell
python download_datasets.py coswara
python -m pip install kaggle
python download_datasets.py coughvid
python download_datasets.py icbhi
```

Coswara is cloned from the official IISc repository. COUGHVID and ICBHI are downloaded from Kaggle using their dataset IDs. Configure `%USERPROFILE%\\.kaggle\\kaggle.json` first and accept each dataset's terms on Kaggle. Do not commit recordings to this repository.

## Local layout for the ML loader

Place labeled files in separate dataset directories:

```text
datasets/
  coswara/<label>/*.wav
  coughvid/<label>/*.wav
  icbhi/<label>/*.wav
```

The directory name is used as a provisional label. Keep dataset-specific experiments separate, split by participant when participant IDs are available, and never use the ECHO personal baseline score as a disease label.

The production ECHO score answers: "Has this person's sound changed from their own baseline?" A disease model answers a different question and requires clinically validated labels, patient-level leakage controls, calibration, and external evaluation.

## Train a first ML experiment

After placing one dataset in the layout above:

```powershell
python ml_train.py datasets/coughvid
```

This trains a small Random Forest on the eight transparent acoustic features and saves it under `models/`. It is a research baseline only. For a credible result, replace the random split with a participant-level split and train separate models for Coswara, COUGHVID, and ICBHI.