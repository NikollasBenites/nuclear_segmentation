#!/bin/bash
set -e
if ! command -v conda >/dev/null 2>&1; then
    for candidate in "$HOME/anaconda3/bin/conda" "$HOME/miniconda3/bin/conda" /opt/anaconda3/bin/conda /opt/miniconda3/bin/conda; do
        if [ -x "$candidate" ]; then
            eval "$("$candidate" shell.bash hook)"
            break
        fi
    done
else
    eval "$(conda shell.bash hook)"
fi
conda activate nuclearapp_latest
python -m nuclear_segmentation
