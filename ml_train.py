from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split

from echo_features import extract_features


def collect_samples(dataset_dir: Path):
    features = []
    labels = []
    for label_dir in sorted(path for path in dataset_dir.iterdir() if path.is_dir()):
        for audio_path in sorted(label_dir.rglob("*.wav")):
            try:
                import soundfile as sf

                audio, sample_rate = sf.read(audio_path, dtype="float32")
                if audio.ndim > 1:
                    audio = audio.mean(axis=1)
                vector, _ = extract_features(audio, sample_rate)
            except Exception as error:
                print(f"Skipping {audio_path}: {error}")
                continue
            features.append(vector)
            labels.append(label_dir.name)
    return np.asarray(features), np.asarray(labels)


def main():
    parser = argparse.ArgumentParser(description="Train a dataset-specific ECHO audio baseline classifier.")
    parser.add_argument("dataset", type=Path, help="Folder such as datasets/coughvid with one subfolder per label")
    parser.add_argument("--output", type=Path, default=Path("models/audio_classifier.joblib"))
    args = parser.parse_args()

    features, labels = collect_samples(args.dataset)
    if len(features) < 4 or len(set(labels)) < 2:
        raise SystemExit("Need at least four readable WAV files across two label folders.")

    train_x, test_x, train_y, test_y = train_test_split(
        features, labels, test_size=0.2, random_state=42, stratify=labels
    )
    model = RandomForestClassifier(n_estimators=200, random_state=42, class_weight="balanced")
    model.fit(train_x, train_y)
    predictions = model.predict(test_x)
    print(classification_report(test_y, predictions, zero_division=0))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "feature_names": list(__import__("echo_features").FEATURE_LABELS)}, args.output)
    args.output.with_suffix(".json").write_text(
        json.dumps({"dataset": str(args.dataset), "labels": sorted(set(labels))}, indent=2),
        encoding="utf-8",
    )
    print(f"Saved model to {args.output}")


if __name__ == "__main__":
    main()
