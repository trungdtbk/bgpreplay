An BGP update generator/replayer. It generates random BGP updates at different rates
or replay updates from a MRT file or live from BGPStream with real or specified rates.
Generated updates can be sent to the console (display only) or a connected speaker.

=======
Install
=======

Install from source
-------------------
* Download source from `https://github.com/trungdtbk/bgpreplay`_
* Run::
  python3 setup.py install

Install with pip
----------------
* Run::
  pip3 install bgpreplay

Install third-party libraries
-----------------------------
BGPReplay requires libraries to run. It needs ExaBGP or YaBGP (agent) to speak BGP. 
Install either ExaBGP or YaBGP as desired and specify the agent via command line.
Please refer to ExaBGP or YaBGP for install instructions.

BGPReplay can generate updates randomly or from an MRT file. If replaying from an online
source i.e. BGPStream is needed, then BGPStream and its Python APIs have to be installed.
Please refer to BGPStream website for installation instructions.

=============
Run BGPReplay
=============

BGPReplay can connect to one BGP speaker which if specified it will send the updates to.

All parmaters are specified via the command line. Parameters can also be set via env.
Each paramter has an equivalent environment variable BGPREPLAY_<uppercase of param>.
For instance the env for --mrt is BGPREPLAY_MRT.
