all: do-nothing

do-nothing:
	@echo "This project doesn't need compiling. Available targets:"
	@echo "    deps - Try to automatically install all dependencies"
	@echo "    install - Install this script and the systemd service"

deps:
	apt-get -y install gstreamer1.0-plugins-good gstreamer1.0-plugins-ugly gstreamer1.0-tools gstreamer1.0-alsa python-rpi.gpi

install: rpi-rx5808-stream.py rpi-rx5808-stream.service
	@echo "==== Stopping existing service, if it exists..."
	sudo systemctl stop rpi-rx5808-stream.service || true
	@echo "==== Installing binary..."
	cp rpi-rx5808-stream.py /usr/local/bin/rpi-rx5808-stream.py
	chmod 774 /usr/local/bin/rpi-rx5808-stream.py
	@echo "==== Installing service..."
	cp rpi-rx5808-stream.service /etc/systemd/system/rpi-rx5808-stream.service
	chmod 665 /etc/systemd/system/rpi-rx5808-stream.service
	@echo "==== Registering service..."
	systemctl daemon-reload
	systemctl enable rpi-rx5808-stream.service
	systemctl start rpi-rx5808-stream.service

