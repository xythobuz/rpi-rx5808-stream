# Raspberry Pi RX5808 5.8GHz video streaming server

This short Python script uses GStreamer to convert a video stream from an V4L2 USB device into a webbrowser-compatible MJPEG stream. This is presented on a webinterface that allows interacting with an RX5808 5.8GHz video receiver via SPI.

## Hardware

Use one of the popular cheap [RX5808](https://www.banggood.com/FPV-5_8G-Wireless-Audio-Video-Receiving-Module-RX5808-p-84775.html) 5.8GHz receiver modules with the [SPI modification done to it](https://github.com/sheaivey/rx5808-pro-diversity/blob/develop/docs/rx5808-spi-mod.md).

To convert the analog video into a digital signal, use one of the many Video4Linux compatible USB devices, [as described in more detail on the LinuxTV pages](https://linuxtv.org/wiki/index.php/Easycap), like [this one](https://www.banggood.com/DC5V-USB-Video-Capture-Card-TV-Tuner-LED-VCR-DVD-Audio-Adapter-Converter-p-1082298.html?rmmds=search).

[![Photo 1](https://i.imgur.com/vvMfBAB.jpg)](https://i.imgur.com/vx5ThJN.jpg)
[![Photo 2](https://i.imgur.com/Ipt6x3k.jpg)](https://i.imgur.com/8iMUOLR.jpg)

## Quick Start

On a Raspberry Pi, with a recent Raspbian installed and the USB video grabber and SPI RX5808 connected, run the following commands:

    sudo apt-get update
    sudo apt-get upgrade
    sudo apt-get install git

    git clone http://xythobuz.de/git/rpi-rx5808-stream
    cd rpi-rx5808-stream
    sudo make deps
    sudo make install

Now, point your browser to your Raspberry Pi (eg. http://raspi-rx5808.local):

[![Screenshot Webinterface](https://i.imgur.com/Onb9Mz6.png)](https://i.imgur.com/ELHhqCN.jpg)

You can check the status, output and control the service with these commands:

    sudo systemctl daemon-reload
    sudo systemctl enable rpi-rx5808-stream.service

    sudo systemctl start rpi-rx5808-stream.service
    sudo systemctl stop rpi-rx5808-stream.service

    sudo systemctl status rpi-rx5808-stream.service
    sudo journalctl -u rpi-rx5808-stream.service
    sudo journalctl -fu rpi-rx5808-stream.service

It will automatically be restarted by systemd after crashes.

## License

Large parts of the included Python server script are based on the work of [srinathava in the raspberry-pi-stream-audio-video project](https://github.com/srinathava/raspberry-pi-stream-audio-video). This in turn was based on the [HTTP live streaming implementation by Jeremy Grosser](http://synack.me/blog/implementing-http-live-streaming).

The Javascript MJPEG player included in the Webinterface is [mjpeg.js made by codebrainz](https://gist.github.com/codebrainz/eeeeead894e8bdff059b).

As parts of the code are heavily based on the [Systemd Watchdog Python example by Spindel](https://gist.github.com/Spindel/1d07533ef94a4589d348), this project is also licensed under the GNU GPLv3:

    Watchdog example code for teaching purposes
    Copyright 2015 D.S. Ljungmark, Modio AB

    This program is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation; either version 3 of the License, or (at your option) any later version.

    This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along with this program; if not, write to the Free Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

