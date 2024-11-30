#!/bin/bash
echo "Start to test the model...."

name="srrawjoint"
dataroot="./SRRAW"

python3 test.py \
    --model srrawjoint  --name $name      --dataset_name srraw  --pre_ispnet_coord False  --gcm_coord True \
    --load_iter 1       --save_imgs True  --calc_metrics True   --gpu_id 0        --visual_full_imgs False \
    --dataroot $dataroot  --scale 4

python3 metrics.py  --name $name --dataroot $dataroot
