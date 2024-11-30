FROM nvcr.io/nvidia/pytorch:24.11-py3
RUN apt-get update
RUN apt-get install -y libgl1
RUN python3 -m pip install colour_demosaicing
RUN python3 -m pip install lpips
RUN python3 -m pip install tensorboardX
RUN python3 -m pip install cupy-cuda12x
RUN python3 -m pip install scikit-image

#RUN python3 -m cupyx.tools.install_library --cuda 12.x --library cudart
CMD ["bash"]
