#!/usr/bin/env python

# Dependency installation:
# sudo apt-get update
# sudo apt-get -y upgrade
# sudo apt-get -y install gstreamer1.0-plugins-good gstreamer1.0-plugins-ugly gstreamer1.0-tools gstreamer1.0-alsa python-rpi.gpio
#
# Enable Hardware Watchdog:
# sudo vi /etc/systemd/system.conf
#   RuntimeWatchdogSec=20
#   ShutdownWatchdogSec=1min
# as described in:
# http://0pointer.de/blog/projects/watchdog.html
#
# Taken from:
# https://github.com/srinathava/raspberry-pi-stream-audio-video/blob/master/mjpeg_server.py
#
# Extended in 2017 by:
# Thomas Buck <xythobuz@xythobuz.de>
#
# Based on the ideas from:
# http://synack.me/blog/implementing-http-live-streaming
#
# Webinterface based in parts on:
# https://gist.github.com/codebrainz/eeeeead894e8bdff059b
#
# Software Systemd Watchdog support based on:
# https://gist.github.com/Spindel/1d07533ef94a4589d348
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from Queue import Queue
from threading import Thread
import socket
from select import select
from wsgiref.simple_server import WSGIServer, make_server, WSGIRequestHandler
from SocketServer import ThreadingMixIn
import subprocess
import os
import signal
import atexit
import RPi.GPIO as GPIO
import time

# -----------------------------------------------------------------------------
# ----- Preferences -----

# Port where the webinterface will be served. The default port 80 requires this
# script to be executed as root (with sudo). Use a higher port number like 8080
# to run it in userspace, or if you already have a webserver installed.
web_port = 81

# Set to None and the first /dev/video* device will be used
video_device = None

# Parameters of your video input device.
# Check beforehand if these work and output a proper image, like this:
#
# gst-launch-1.0 -v \
#     v4l2src device=/dev/video0 norm=NTSC-M \
#         ! video/x-raw, framerate=30000/1001, width=720, height=480 \
#         ! videorate \
#         ! video/x-raw, framerate=1/1 \
#         ! jpegenc \
#         ! filesink location=test.jpeg
#
# GStreamer unfortunately won't output proper help and just exit
# if your settings don't match here. Try querying available settings:
#
# gst-launch-1.0 --gst-debug=v4l2src:5 \
#     v4l2src device=/dev/video0 \
#         ! fakesink 2>&1 \
#     | sed -une '/caps of src/ s/[:;] /\n/gp'
#
# (replace /dev/video0 in the command with your input device)
video_width = 720
video_height = 480
video_framerate = '30000/1001'
video_norm = 'NTSC'

# The MJPEG output framerate fraction is determined by this setting. For old
# models like my Raspberry Pi 1, keep this value low, like '1/1' (1FPS).
# Faster framerates could be eg. '10/1' (10FPS).
video_out_framerate = '10/1'

# Time in ms between image redraws for the JS MJPEG client. Should be less than
# or equal to the time between frames / 2 for your chosen video_out_framerate.
client_video_refresh_rate = 50

# Boundary between MJPEG mutlipart frames. Can be set to any random string.
boundary_string = "raspberrypi-rx5808-stream-xythobuz"

# Size of the canvas of the image shown in the webinterface.
canvas_width = video_width
canvas_height = video_height

# Parameters for your audio input device.
# Test the audio recording like this:
#
# gst-launch-1.0 -v \
#     alsasrc device=hw:CARD=usbtv,DEV=0 \
#     ! audio/x-raw, channels=2, rate=48000 \
#     ! lamemp3enc target=bitrate bitrate=64 mono=true \
#     ! filesink location=test.mp3
#
# Query the available settings like this:
#
# gst-launch-1.0 --gst-debug=alsa:5 \
#     alsasrc device=hw:CARD=usbtv,DEV=0 \
#     ! fakesink 2>&1 \
#     | sed -une '/returning caps/  s/[s;] /\n/gp'
#
# (replace hw:CARD=usbtv,DEV=0 with your hardware identifier string)
audio_device = "hw:CARD=usbtv,DEV=0"
audio_channels = "2"
audio_rate = "48000"

# MP3 output bitrate. Valid values here are:
# 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256 or 320
audio_mp3_bitrate = "96"

# RX5808 GPIOs; use board numbering, so pin number of header
pin_ch1 = 15
pin_ch2 = 13
pin_ch3 = 11

# Maximum number of clients that are allowed to be connected to the MJPEG and
# MP3 stream at the same time. Any more will get a 503 with a proper message.
# Set to at least 4 for two browser connections (2xaudio + 2xvideo).
# Set this to zero to disable the check.
# This setting is especially useful for less powerful old Raspberry Pi models.
# I'm using a RPi1 and with more than 1 FPS or more than 1 client the CPU usage
# rises over 100% and everything pretty much stands still.
maximum_clients = 4

# Audio & Video are out-of-sync by definition when using this technique.
# Audio will be preloaded and the video only started when audio is ready.
# This helps with the sync, but isn't perfect and causes a pretty big delay when
# starting the stream.
play_video_immediately = True
autoplay_stream = True

# Where the image data will be streamed from gstreamer and read from this script.
# Currently, video_host can only be localhost!
# Change the port if you have a conflict with some other running application.
video_host = '127.0.0.1'
video_port = 9999

audio_host = '127.0.0.1'
audio_port = 9998

# -----------------------------------------------------------------------------
# ----- Automatic /dev/video* device search -----

video_device_searched = None

def determineVideoDevice():
    global video_device_searched

    if video_device != None:
        video_device_searched = video_device
        print("Selected \"{}\" as video device...".format(video_device_searched))
        return

    path = "/dev/"
    files = []
    for i in os.listdir(path):
        f = os.path.join(path, i)
        if "/dev/video" in f:
            files.append(f)

    if len(files) > 0:
        video_device_searched = files[0]
        print("Selected \"{}\" as video device...".format(video_device_searched))
    else:
        video_device_searched = None
        print("No video device found!")
        os.abort()

determineVideoDevice() # run once at beginning so we see device before first stream

# -----------------------------------------------------------------------------
# ----- Webinterface template pages -----

lastCommandResult = None

def buildIndexPage(environ):
    global lastCommandResult

    page_text = """
<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>RX5808 Stream</title>
  </head>
  <body>
    <h1>Raspberry Pi RX5808 Video Stream</h1>
"""

    if lastCommandResult != None:
        page_text += "<p>Last command returned result: {}</p>".format(lastCommandResult)
        lastCommandResult = None

    page_text += """
    <hr />
    <table>
      <tr>
        <td>
          <canvas id="player" width=""" + '"' + str(canvas_width) + '" height="' + str(canvas_height) + '"' + """ style="background: #000; width: """ + str(canvas_width) + "px; height: " + str(canvas_height) + 'px;"' + """>
            <noscript>
              <img src="/mjpeg_stream" width=""" + '"' + str(canvas_width) + '" height="' + str(canvas_height) + '"' + """ />
              <p>Use a modern browser with Javascript and HTML5 support to enable playback controls.</p>
            </noscript>
          </canvas>
          <audio id="audio_player">
            <noscript>
              <p>Use a modern browser with Javascript and HTML5 support to enable audio playback.</p>
            </noscript>
          </audio>
          <p><a href="/mjpeg_stream">Link to MJPEG video stream</a> <a href="/mp3_stream">Link to MP3 audio stream</a></p>
          <p>Click on video frame to play or pause stream:</p>
          <div id="status">
            <p>Stream status: Loading...</p>
          </div>
        </td><td>
          <p>Audio Volume: <input id="volume_control" type="range" min="0" max="100" step="1" oninput="SetVolume(this.value)" onchange="SetVolume(this.value)"></input> <span id="volume_text" /></p>
          <hr />
          <p>Currently selected Frequency: <b>""" + get_frequency() + """</b> """ + get_osc_settings() + """</p>
          <hr />
          <p>RX5808 Frequency selection:</p>
          <table border="1">
            <tr>
              <th>Channel 1</th>
              <th>Channel 2</th>
              <th>Channel 3</th>
              <th>Channel 4</th>
              <th>Channel 5</th>
              <th>Channel 6</th>
              <th>Channel 7</th>
              <th>Channel 8</th>
              <th>Channel / Band</th>
            </tr><tr>
              <td><a href="?freq=5658MHz">5658MHz</a></td>
              <td><a href="?freq=5695MHz">5695MHz</a></td>
              <td><a href="?freq=5732MHz">5732MHz</a></td>
              <td><a href="?freq=5769MHz">5769MHz</a></td>
              <td><a href="?freq=5806MHz">5806MHz</a></td>
              <td><a href="?freq=5843MHz">5843MHz</a></td>
              <td><a href="?freq=5880MHz">5880MHz</a></td>
              <td><a href="?freq=5917MHz">5917MHz</a></td>
              <th>Raceband</th>
            </tr><tr>
              <td><a href="?freq=5865MHz">5865MHz</a></td>
              <td><a href="?freq=5845MHz">5845MHz</a></td>
              <td><a href="?freq=5825MHz">5825MHz</a></td>
              <td><a href="?freq=5805MHz">5805MHz</a></td>
              <td><a href="?freq=5785MHz">5785MHz</a></td>
              <td><a href="?freq=5765MHz">5765MHz</a></td>
              <td><a href="?freq=5745MHz">5745MHz</a></td>
              <td><a href="?freq=5725MHz">5725MHz</a></td>
              <th>Band A</th>
            </tr><tr>
              <td><a href="?freq=5733MHz">5733MHz</a></td>
              <td><a href="?freq=5752MHz">5752MHz</a></td>
              <td><a href="?freq=5771MHz">5771MHz</a></td>
              <td><a href="?freq=5790MHz">5790MHz</a></td>
              <td><a href="?freq=5809MHz">5809MHz</a></td>
              <td><a href="?freq=5828MHz">5828MHz</a></td>
              <td><a href="?freq=5847MHz">5847MHz</a></td>
              <td><a href="?freq=5866MHz">5866MHz</a></td>
              <th>Band B</th>
            </tr><tr>
              <td><a href="?freq=5705MHz">5705MHz</a></td>
              <td><a href="?freq=5685MHz">5685MHz</a></td>
              <td><a href="?freq=5665MHz">5665MHz</a></td>
              <td><a href="?freq=5645MHz">5645MHz</a></td>
              <td><a href="?freq=5885MHz">5885MHz</a></td>
              <td><a href="?freq=5905MHz">5905MHz</a></td>
              <td><a href="?freq=5925MHz">5925MHz</a></td>
              <td><a href="?freq=5945MHz">5945MHz</a></td>
              <th>Band E</th>
            </tr><tr>
              <td><a href="?freq=5740MHz">5740MHz</a></td>
              <td><a href="?freq=5760MHz">5760MHz</a></td>
              <td><a href="?freq=5780MHz">5780MHz</a></td>
              <td><a href="?freq=5800MHz">5800MHz</a></td>
              <td><a href="?freq=5820MHz">5820MHz</a></td>
              <td><a href="?freq=5840MHz">5840MHz</a></td>
              <td><a href="?freq=5860MHz">5860MHz</a></td>
              <td><a href="?freq=5880MHz">5880MHz</a></td>
              <th>Band F / Airwave</th>
            </tr><tr>
              <td><a href="?freq=5362MHz">5362MHz</a></td>
              <td><a href="?freq=5399MHz">5399MHz</a></td>
              <td><a href="?freq=5436MHz">5436MHz</a></td>
              <td><a href="?freq=5473MHz">5473MHz</a></td>
              <td><a href="?freq=5510MHz">5510MHz</a></td>
              <td><a href="?freq=5547MHz">5547MHz</a></td>
              <td><a href="?freq=5584MHz">5584MHz</a></td>
              <td><a href="?freq=5621MHz">5621MHz</a></td>
              <th>Band D / 5.3</th>
            </tr>
          </table>
          <hr />
          <p>Video properties:</p>
          <ul>
            <li>Input device: """ + str(video_device_searched) + """</li>
            <li>Video format: """ + str(video_norm) + """</li>
            <li>Input resolution: """ + str(video_width) + """x""" + str(video_height) + """</li>
            <li>Input framerate: """ + str(video_framerate) + """</li>
            <li>Output framerate: """ + str(video_out_framerate) + """</li>
          </ul>
        </td>
      </tr>
    </table>
    <hr />
    <p>Current Status (not updated dynamically, refresh to reload!):</p>
    <p>Streaming clients: """ + str(client_count)

    page_text += """ (should be zero when no one else is watching)</p>
    <p>Linux Status: """ + runCommand("uptime") + "</p>"

    cpu = int(runCommand("cat /sys/class/thermal/thermal_zone0/temp"))
    cpu1 = cpu / 1000
    cpu2 = cpu / 100
    cpuM = cpu2 % cpu1
    cpu = str(cpu1) + "." + str(cpuM) + "'C"
    gpu = runCommand("vcgencmd measure_temp | sed 's/temp=//'")
    page_text += "<p>Temperatures CPU: {} GPU: {}</p>".format(cpu, gpu)

    page_text += """
    <p><a href="?quit">Restart RX5808 Streaming Server</a></p>
    <p><a href="?reboot">Reboot Raspberry Pi</a></p>
    <hr />
    <p style="font-size: small; text-align: center;">Version 0.3 - Made by <a href="http://xythobuz.de">Thomas Buck &lt;xythobuz@xythobuz.de&gt;</a></p>
  </body>
  <script type="text/javascript">
var MJPEG = (function(module) {
  "use strict";

  module.Stream = function(args) {
    var self = this;
    var autoStart = args.autoStart || false;

    self.url = args.url;
    self.refreshRate = args.refreshRate || """ + str(client_video_refresh_rate) + """;
    self.onStart = args.onStart || null;
    self.onFrame = args.onFrame || null;
    self.onStop = args.onStop || null;
    self.callbacks = {};
    self.running = false;
    self.frameTimer = 0;

    self.img = new Image();
    if (autoStart) {
      self.img.onload = self.start;
    }
    self.img.src = self.url;

    function setRunning(running) {
      self.running = running;
      if (self.running) {
        self.img.src = self.url;
        self.frameTimer = setInterval(function() {
          if (self.onFrame) {
            self.onFrame(self.img);
          }
        }, self.refreshRate);
        if (self.onStart) {
          self.onStart();
        }
      } else {
        self.img.src = '';

        if (window.stop !== undefined) {
          window.stop();
        } else if (document.execCommand !== undefined) {
          document.execCommand("Stop", false);
        }

        clearInterval(self.frameTimer);

        if (self.onStop) {
          self.onStop();
        }
      }
    }

    self.start = function() { setRunning(true); }
    self.stop = function() { setRunning(false); }
  };

  module.Player = function(canvas, url, audio_player, audio_url, options) {

    var self = this;

    if (typeof canvas === "string" || canvas instanceof String) {
      canvas = document.getElementById(canvas);
    }

    var context = canvas.getContext("2d");

    if (! options) {
      options = {};
    }
    options.url = url;
    options.onFrame = updateFrame;
    options.onStart = function() { console.log("MJPEG stream started"); }
    options.onStop = function() { console.log("MJPEG stream stopped"); }

    self.stream = new module.Stream(options);

    self.status = document.getElementById("status");

    canvas.addEventListener("click", function() {
      if (self.stream.running) {
        self.stop();
      } else {
        self.start();
      }
    }, false);

    if (typeof audio_player === "string" || audio_player instanceof String) {
      audio_player = document.getElementById(audio_player);
    }

    var volume_control = document.getElementById('volume_control');
    var volume_text = document.getElementById('volume_text');

    // Set default volume
    volume_control.value = 10;
    volume_text.innerHTML = "10%"
    audio_player.volume = 0.1;

    function scaleRect(srcSize, dstSize) {
      var ratio = Math.min(dstSize.width / srcSize.width,
                           dstSize.height / srcSize.height);
      var newRect = {
        x: 0, y: 0,
        width: srcSize.width * ratio,
        height: srcSize.height * ratio
      };
      newRect.x = (dstSize.width/2) - (newRect.width/2);
      newRect.y = (dstSize.height/2) - (newRect.height/2);
      return newRect;
    }

    function updateFrame(img) {
      var srcRect = {
        x: 0, y: 0,
        width: img.naturalWidth,
        height: img.naturalHeight
      };
      var dstRect = scaleRect(srcRect, {
        width: canvas.width,
        height: canvas.height
      });
      try {
        context.drawImage(img,
          srcRect.x,
          srcRect.y,
          srcRect.width,
          srcRect.height,
          dstRect.x,
          dstRect.y,
          dstRect.width,
          dstRect.height
        );
      } catch (e) {
        // if we can't draw, don't bother updating anymore
        self.stop();
        console.log("!");
        throw e;
      }
    }

    function canvasOverlay(r, g, b, a) {
      var imageData = context.getImageData(0, 0, canvas.width, canvas.height);

      var fr = r / 255.0;
      var fg = g / 255.0;
      var fb = b / 255.0;
      var fa = a / 255.0;

      for (var i = 0; i < imageData.data.length; i += 4) {
        imageData.data[i + 0] = (fr * fa) + ((1.0 - fa) * (imageData.data[i + 0] / 255.0)) * 255.0;
        imageData.data[i + 1] = (fg * fa) + ((1.0 - fa) * (imageData.data[i + 1] / 255.0)) * 255.0;
        imageData.data[i + 2] = (fb * fa) + ((1.0 - fa) * (imageData.data[i + 2] / 255.0)) * 255.0;
      }

      context.putImageData(imageData, 0, 0);
    }

    function canvasText(str, y_off = 0, text_size = 16) {
      context.fillStyle = "white";
      context.font = "bold " + text_size + "px Arial";
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText(str, canvas.width / 2, canvas.height / 2 + y_off);
    }

    self.start = function() {
      self.status.innerHTML = "<p>Stream status: Audio Started!</p>";

      canvasText("Loading stream...", 10);
      canvasText('URL: "' + url + '"', -canvas.height / 2 + 10, 12);

      audio_player.src = audio_url;
      audio_player.play();
"""

    if not play_video_immediately:
        page_text += """
      audio_player.oncanplay = self.started_audio;
    }

    self.started_audio = function() {
      self.status.innerHTML = "<p>Stream status: Video Started!</p>";
      self.stream.start();
"""
    else:
        page_text += """
      self.status.innerHTML = "<p>Stream status: Video Started!</p>";
      self.stream.start();
"""

    page_text += """
    }

    self.stop = function() {
      audio_player.pause();
      audio_player.src = '';

      self.stream.stop();
      self.status.innerHTML = "<p>Stream status: Stopped!</p>";

      canvasOverlay(0, 0, 0, 127);
      canvasText("Click to Play...", -10);
    }
  };

  return module;
})(MJPEG || {});

var url = window.location.protocol + "//" + window.location.hostname + ":" + window.location.port + "/mjpeg_stream";
var audio_url = window.location.protocol + "//" + window.location.hostname + ":" + window.location.port + "/mp3_stream";

console.log("Connecting to: " + url);
console.log("Connecting to: " + audio_url);

window.history.pushState(null, null, '/');

var player = new MJPEG.Player("player", url, "audio_player", audio_url);

"""

    if autoplay_stream:
        page_text += "player.start();"
    else:
        page_text += "player.stop();"

    page_text += """
function SetVolume(val) {
  var player = document.getElementById('audio_player');
  var volume_text = document.getElementById('volume_text');

  player.volume = val / 100.0;
  volume_text.innerHTML = String(val) + "%"

  console.log('New volume: ' + player.volume);
}
  </script>
</html>
"""
    return page_text

def buildErrorPage(environ, error, title, text = None):
    page_text = """
<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>""" + error + """ - """ + title + """</title>
  </head>
  <body>
    <h1>""" + error + """ - """ + title + """</h1>
"""

    if text != None:
        page_text += "<p>" + text + "</p>"

    page_text += """
    <p>Try the <a href="/">homepage</a>...</p>
  </body>
</html>
"""

    return page_text

# -----------------------------------------------------------------------------
# ----- RX5808 SPI GPIO interface -----

pin_data = pin_ch1
pin_ss = pin_ch2
pin_clock = pin_ch3

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BOARD)
GPIO.setup(pin_data, GPIO.OUT)
GPIO.setup(pin_ss, GPIO.OUT)
GPIO.setup(pin_clock, GPIO.OUT)

channel_values = [
    # Channel 1 - 8
    0x281D, 0x288F, 0x2902, 0x2914, 0x2987, 0x2999, 0x2A0C, 0x2A1E, # Raceband
    0x2A05, 0x299B, 0x2991, 0x2987, 0x291D, 0x2913, 0x2909, 0x289F, # Band A
    0x2903, 0x290C, 0x2916, 0x291F, 0x2989, 0x2992, 0x299C, 0x2A05, # Band B
    0x2895, 0x288B, 0x2881, 0x2817, 0x2A0F, 0x2A19, 0x2A83, 0x2A8D, # Band E
    0x2906, 0x2910, 0x291A, 0x2984, 0x298E, 0x2998, 0x2A02, 0x2A0C, # Band F / Airwave
    0x2609, 0x261C, 0x268E, 0x2701, 0x2713, 0x2786, 0x2798, 0x280B # Band D / 5.3
]

channel_frequencies = [
    # Channel 1 - 8
    5658, 5695, 5732, 5769, 5806, 5843, 5880, 5917, # Raceband
    5865, 5845, 5825, 5805, 5785, 5765, 5745, 5725, # Band A
    5733, 5752, 5771, 5790, 5809, 5828, 5847, 5866, # Band B
    5705, 5685, 5665, 5645, 5885, 5905, 5925, 5945, # Band E
    5740, 5760, 5780, 5800, 5820, 5840, 5860, 5880, # Band F / Airwave
    5362, 5399, 5436, 5473, 5510, 5547, 5584, 5621 # Band D / 5.3
]

def spi_sendbit_1():
    GPIO.output(pin_clock, GPIO.LOW)
    time.sleep(0.000001)

    GPIO.output(pin_data, GPIO.HIGH)
    time.sleep(0.000001)
    GPIO.output(pin_clock, GPIO.HIGH)
    time.sleep(0.000001)

    GPIO.output(pin_clock, GPIO.LOW)
    time.sleep(0.000001)

def spi_sendbit_0():
    GPIO.output(pin_clock, GPIO.LOW)
    time.sleep(0.000001)

    GPIO.output(pin_data, GPIO.LOW)
    time.sleep(0.000001)
    GPIO.output(pin_clock, GPIO.HIGH)
    time.sleep(0.000001)

    GPIO.output(pin_clock, GPIO.LOW)
    time.sleep(0.000001)

def spi_readbit():
    GPIO.output(pin_clock, GPIO.LOW)
    time.sleep(0.000001)

    GPIO.output(pin_clock, GPIO.HIGH)
    time.sleep(0.000001)

    if GPIO.input(pin_data) == GPIO.HIGH:
        return True
    else:
        return False

    GPIO.output(pin_clock, GPIO.LOW)
    time.sleep(0.000001)

def spi_select_low():
    time.sleep(0.000001)
    GPIO.output(pin_ss, GPIO.LOW)
    time.sleep(0.000001)

def spi_select_high():
    time.sleep(0.000001)
    GPIO.output(pin_ss, GPIO.HIGH)
    time.sleep(0.000001)

def get_register(reg):
    spi_select_high();
    time.sleep(0.000001)
    spi_select_low();

    for i in range(4):
        if reg & (1 << i):
            spi_sendbit_1();
        else:
            spi_sendbit_0();

    # Read from register
    spi_sendbit_0();

    GPIO.setup(pin_data, GPIO.IN)

    data = 0
    for i in range(20):
        # Is bit high or low?
        val = spi_readbit()
        if val:
            data |= (1 << i)

    # Finished clocking data in
    spi_select_high()
    time.sleep(0.000001)

    GPIO.setup(pin_data, GPIO.OUT)

    GPIO.output(pin_ss, GPIO.LOW)
    GPIO.output(pin_clock, GPIO.LOW)
    GPIO.output(pin_data, GPIO.LOW)

    return data

def get_frequency():
    channel_data = get_register(0x01)

    channel_freq = None
    for i in range(len(channel_values)):
        if channel_values[i] == channel_data:
            channel_freq = channel_frequencies[i]
            break

    if channel_freq == None:
        return "Unknown ({})".format(hex(channel_data))
    else:
        return str(channel_freq) + "MHz"

def get_osc_settings():
    val = get_register(0x08)
    pre = get_register(0x00)
    return "(Settings: {}; Reference: {}MHz)".format(hex(val), str(pre))

def set_register(reg, val):
    spi_select_high();
    time.sleep(0.000001)
    spi_select_low();

    for i in range(4):
        if reg & (1 << i):
            spi_sendbit_1();
        else:
            spi_sendbit_0();

    # Write to register
    spi_sendbit_1();

    # D0-D15
    for i in range(20):
        # Is bit high or low?
        if val & 0x1:
            spi_sendbit_1()
        else:
            spi_sendbit_0()

        # Shift bits along to check the next one
        val >>= 1

    # Finished clocking data in
    spi_select_high()
    time.sleep(0.000001)
    spi_select_low();

def set_frequency(freq):
    channel_data = None
    for i in range(len(channel_frequencies)):
        if str(channel_frequencies[i]) == freq:
            channel_data = channel_values[i]
            break

    if channel_data == None:
        s = "Error: unknown frequency {}MHz!".format(freq)
        print(s)
        return s

    print("Selected frequency: {}MHz ({})...".format(freq, channel_data))

    #set_register(0x08, 0x00)
    set_register(0x08, 0x03F40) # default values

    set_register(0x01, channel_data)

    GPIO.output(pin_ss, GPIO.LOW)
    GPIO.output(pin_clock, GPIO.LOW)
    GPIO.output(pin_data, GPIO.LOW)

    return "Success (set freq to {})!".format(hex(channel_data))

# -----------------------------------------------------------------------------
# ----- Webinterface GET parameter handling -----

def handleSettings(queryString):
    global lastCommandResult

    if queryString == "reboot":
        runCommand("sudo shutdown -r now")
    elif queryString.startswith("freq=") and queryString.endswith("MHz"):
        freq = queryString.replace("freq=", "").replace("MHz", "")
        lastCommandResult = set_frequency(freq)
        time.sleep(0.1)
    elif queryString == "quit":
        print("Exiting after user request!")
        kill_all()
    else:
        print("Got unknown query string: \"{}\"".format(queryString))

# -----------------------------------------------------------------------------
# ----- External Process / GStreamer control -----

def runCommand(cmd):
    return subprocess.check_output(cmd, shell = True)

def buildGStreamerCommand():
    global video_device_searched, video_norm, video_framerate, video_width, video_height

    return ("exec gst-launch-1.0 " #-v "
        "v4l2src device=" + str(video_device_searched) + " norm=" + str(video_norm) + " "
        #"videotestsrc pattern=ball "
        "! video/x-raw, framerate=" + str(video_framerate) + ", width=" + str(video_width) + ", height=" + str(video_height) + " "
        "! videorate "
        "! video/x-raw, framerate=" + str(video_out_framerate) + " "
        "! jpegenc "
        "! multipartmux boundary=" + str(boundary_string) + " "
        "! tcpclientsink host=" + str(video_host) + " port=" + str(video_port) + " "
        "alsasrc device=" + str(audio_device) + " "
        "! audio/x-raw, channels=" + str(audio_channels) + ", rate=" + str(audio_rate) + " "
        "! lamemp3enc target=bitrate bitrate=" + str(audio_mp3_bitrate) + " mono=true "
        "! tcpclientsink host=" + str(audio_host) + " port=" + str(audio_port) + " "
    )

last_proc = None

def runGStreamer():
    global last_proc

    determineVideoDevice()

    last_proc = subprocess.Popen(args = buildGStreamerCommand(), stdin = subprocess.PIPE, stderr = subprocess.PIPE, shell = True)

def killGStreamer():
    global last_proc

    if last_proc != None:
        last_proc.kill()
        time.sleep(0.1)

        os.kill(last_proc.pid, signal.SIGINT)
        time.sleep(0.1)

        os.kill(last_proc.pid, signal.SIGTERM)
        time.sleep(0.05)
        os.kill(last_proc.pid, signal.SIGKILL)

# -----------------------------------------------------------------------------
# ----- Webserver -----

class MyWSGIServer(ThreadingMixIn, WSGIServer):
     pass

def create_server(host, port, app, server_class=MyWSGIServer,
          handler_class=WSGIRequestHandler):
     return make_server(host, port, app, server_class, handler_class)

client_count = 0
thread_running = True

class IPCameraApp(object):
    queues = []
    audio_queues = []

    def __call__(self, environ, start_response):
        if environ['PATH_INFO'] == '/':
            if environ['QUERY_STRING']:
                handleSettings(environ['QUERY_STRING'])
            index_page_contents = buildIndexPage(environ)
            start_response("200 OK", [
                ("Content-Type", "text/html"),
                ("Content-Length", str(len(index_page_contents)))
            ])
            return iter([index_page_contents])
        elif environ['PATH_INFO'] == '/mjpeg_stream':
            if (maximum_clients == 0) or (client_count < maximum_clients):
                return self.stream(start_response)
            else:
                text = "No streaming slots available ({}/{})!".format(client_count, maximum_clients)
                error_page_contents = buildErrorPage(environ, "503", "Service Unavailable", text)
                start_response("503 Service Unavailable", [
                    ("Content-Type", "text/html"),
                    ("Content-Length", str(len(error_page_contents)))
                ])
                return iter([error_page_contents])
        elif environ['PATH_INFO'] == '/mp3_stream':
            if (maximum_clients == 0) or (client_count < maximum_clients):
                return self.stream_audio(start_response)
            else:
                text = "No streaming slots available ({}/{})!".format(client_count, maximum_clients)
                error_page_contents = buildErrorPage(environ, "503", "Service Unavailable", text)
                start_response("503 Service Unavailable", [
                    ("Content-Type", "text/html"),
                    ("Content-Length", str(len(error_page_contents)))
                ])
                return iter([error_page_contents])
        else:
            error_page_contents = buildErrorPage(environ, "404", "Not Found")
            start_response("404 Not Found", [
                ("Content-Type", "text/html"),
                ("Content-Length", str(len(error_page_contents)))
            ])
            return iter([error_page_contents])

    # MJPEG client Thread
    def stream(self, start_response):
        global thread_running, client_count

        print("StreamOutput: Started streaming to a client...")

        # Start GStreamer if this is the first client
        client_count += 1
        if client_count == 1:
            print("StreamOutput: First client, starting GStreamer...")
            runGStreamer()

        start_response('200 OK', [('Content-type', 'multipart/x-mixed-replace; boundary=' + boundary_string)])

        q = Queue()
        self.queues.append(q)

        while thread_running:
            try:
                yield q.get()
            except:
                if q in self.queues:
                    self.queues.remove(q)
                break

        print("StreamOutput: Stopped streaming to a client...")

        # Stop GStreamer if this was the last client
        client_count -= 1
        if client_count == 0:
            print("StreamOutput: Last client, stopping GStreamer...")
            killGStreamer()

    # MP3 client Thread
    def stream_audio(self, start_response):
        global thread_running, client_count

        print("StreamAudioOutput: Started streaming to a client...")

        # Start GStreamer if this is the first client
        client_count += 1
        if client_count == 1:
            print("StreamOutput: First client, starting GStreamer...")
            runGStreamer()

        start_response('200 OK', [('Content-type', 'audio/mpeg')])

        q = Queue()
        self.audio_queues.append(q)

        while thread_running:
            try:
                yield q.get()
            except:
                if q in self.audio_queues:
                    self.audio_queues.remove(q)
                break

        print("StreamAudioOutput: Stopped streaming to a client...")

        # Stop GStreamer if this was the last client
        client_count -= 1
        if client_count == 0:
            print("StreamOutput: Last client, stopping GStreamer...")
            killGStreamer()

# -----------------------------------------------------------------------------
# ----- MJPEG / MP3 receiver -----

def input_loop(app):
    global thread_running, client_count

    sock = socket.socket()
    sock.bind((video_host, video_port))
    sock.listen(1)

    while thread_running:
        print("StreamInput: Waiting for input stream on port {}...".format(video_port))

        sd, addr = sock.accept()
        print("StreamInput: Accepted input stream from {}...".format(addr))

        data = True
        while data:
            readable = select([sd], [], [], 0.1)[0]
            for s in readable:
                data = s.recv(1024)
                if not data:
                    break
                for q in app.queues:
                    q.put(data)

        print("StreamInput: Lost input stream from {}!".format(addr))

        # Only restart when clients are listening
        if (client_count > 0) and thread_running:
            print("StreamInput: Restarting GStreamer child process...")
            time.sleep(0.25)
            runGStreamer()

    print("StreamInput: Goodbye...")

def audio_input_loop(app):
    global thread_running

    sock = socket.socket()
    sock.bind((audio_host, audio_port))
    sock.listen(1)

    while thread_running:
        print("StreamAudioInput: Waiting for input stream on port {}...".format(audio_port))

        sd, addr = sock.accept()
        print("StreamAudioInput: Accepted input stream from {}...".format(addr))

        data = True
        while data:
            readable = select([sd], [], [], 0.1)[0]
            for s in readable:
                data = s.recv(1024)
                if not data:
                    break
                for q in app.audio_queues:
                    q.put(data)

        print("StreamAudioInput: Lost input stream from {}!".format(addr))

    print("StreamAudioInput: Goodbye...")

# -----------------------------------------------------------------------------
# ----- systemd watchdog interface -----

def watchdog_period():
    """Return the time (in seconds) that we need to ping within."""
    val = os.environ.get("WATCHDOG_USEC", None)
    if not val:
        return None
    return int(val)/1000000


def notify_socket(clean_environment=True):
    """Return a tuple of address, socket for future use.
    clean_environment removes the variables from env to prevent children
    from inheriting it and doing something wrong.
    """
    _empty = None, None
    address = os.environ.get("NOTIFY_SOCKET", None)
    if clean_environment:
        address = os.environ.pop("NOTIFY_SOCKET", None)

    if not address:
        return _empty

    if len(address) == 1:
        return _empty

    if address[0] not in ("@", "/"):
        return _empty

    if address[0] == "@":
        address = "\0" + address[1:]

    # SOCK_CLOEXEC was added in Python 3.2 and requires Linux >= 2.6.27.
    # It means "close this socket after fork/exec()
    try:
        sock = socket.socket(socket.AF_UNIX,
                             socket.SOCK_DGRAM | socket.SOCK_CLOEXEC)
    except AttributeError:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)

    return address, sock


def sd_message(address, sock, message):
    """Send a message to the systemd bus/socket.
    message is expected to be bytes.
    """
    if not (address and sock and message):
        return False
    assert isinstance(message, bytes)

    try:
        retval = sock.sendto(message, address)
    except socket.error:
        return False
    return (retval > 0)


def watchdog_ping(address, sock):
    """Helper function to send a watchdog ping."""
    message = b"WATCHDOG=1"
    return sd_message(address, sock, message)

def systemd_ready(address, sock):
    """Helper function to send a ready signal."""
    message = b"READY=1"
    return sd_message(address, sock, message)

def systemd_status(address, sock, status):
    """Helper function to update the service status."""
    message = ("STATUS=%s" % status).encode('utf8')
    return sd_message(address, sock, message)

notify = notify_socket()
period = watchdog_period()

def watchdog_ready():
    if notify:
        systemd_ready(*notify)

def watchdog_status(stat):
    if notify:
        systemd_status(*notify, status=stat)

def watchdog_loop(app):
    if not notify[0]:
        print("StreamWatchdog: No notification socket, not launched via systemd?")
        return

    if not period:
        print("StreamWatchdog: No watchdog period set in the unit file.")
        return

    print("StreamWatchdog: Enabling Systemd Watchdog...")

    while thread_running:
        watchdog_ping(*notify)
        time.sleep(period / 2.0)

# -----------------------------------------------------------------------------
# ----- main application logic -----

def kill_all():
    global thread_running

    thread_running = False
    killGStreamer()

if __name__ == '__main__':
    watchdog_status(b"Initializing stream...")

    app = IPCameraApp()

    print("StreamServer: Launching Webserver on port {}...".format(web_port))
    httpd = create_server('', web_port, app)

    print("StreamServer: Launching input stream thread...")
    t1 = Thread(target=input_loop, args=[app])
    t1.setDaemon(True)
    t1.start()

    print("StreamServer: Launching audio input stream thread...")
    t2 = Thread(target=audio_input_loop, args=[app])
    t2.setDaemon(True)
    t2.start()

    print("StreamServer: Launching watchdog thread...")
    t3 = Thread(target=watchdog_loop, args=[app])
    t3.setDaemon(True)
    t3.start()

    print("StreamServer: Waiting for connections to start streaming...")
    atexit.register(kill_all)

    watchdog_ready()
    watchdog_status(b"Mainloop started, serving content.")

    try:
        print("StreamServer: Serving HTTP content...")
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("StreamServer: killing threads and child process...")
        kill_all()
        print("StreamServer: stopping HTTP server...")
        httpd.shutdown()
        print("StreamServer: Goodbye...")

