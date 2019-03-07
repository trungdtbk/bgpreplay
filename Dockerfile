FROM ubuntu:14.04

RUN apt-get update --fix-missing
RUN apt-get upgrade -y
RUN apt-get install -y --no-install-recommends \
    build-essential \
    net-tools \
    ifupdown \
    iputils-ping \
    wget \
    unzip \
    git

RUN apt-get install -y python-dev python3-setuptools
RUN apt-get install -y python-pip
RUN pip install --upgrade pip

WORKDIR /
# Install ExaBGP
RUN pip install -I -U exabgp

# Install YaBGP
RUN git clone https://github.com/trungdtbk/yabgp
RUN \
    cd yabgp \
    && pip install -I -U -r requirements.txt \
    && python setup.py install 

COPY ./ /bgpreplay
RUN \
    cd /bgpreplay \
    && python3 setup.py install

CMD ["bgpreplay"]
