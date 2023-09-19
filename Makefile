VER := $(shell grep __ver luadox/version.py  | cut -d\" -f2)
# Name of the release archive without extension
ARCHIVE := luadox-$(VER)

build/luadox: build/pkg.zip
	echo '#!/usr/bin/env python3' > build/luadox
	cat build/pkg.zip >> build/luadox
	chmod 755 build/luadox

build/pkg: luadox/*
	mkdir -p build/pkg/luadox
	cp -a luadox/* build/pkg/luadox
	echo "from luadox.main import main; main()" > build/pkg/__main__.py
	touch build/pkg

build/pkg/ext: requirements.txt
	@echo "*** installing dependencies into $@/"
	pip3 install -t ./build/pkg/ext -r requirements.txt
	@# These aren't needed
	rm -rf build/pkg/ext/bin build/pkg/ext/*.dist-info build/pkg/ext/*/*.so

build/pkg.zip: build/pkg build/pkg/ext
	@echo "*** creating bundle at build/luadox"
	find build -type d -name __pycache__ -prune -exec rm -rf "{}" \;
	cd build/pkg && zip -q -r ../pkg.zip .

.PHONY: docker
docker:
	docker build --pull -t luadox:latest .

.PHONY: release
release: build/luadox
	cd build && tar zcf $(ARCHIVE).tar.gz luadox
	cd build && zip $(ARCHIVE).zip luadox

.PHONY: clean
clean:
	rm -rf build
