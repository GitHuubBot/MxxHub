#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

brew list xcodegen >/dev/null 2>&1 || brew install xcodegen
brew list make >/dev/null 2>&1 || brew install make
bash Scripts/prepare_wineglass.sh
xcodegen generate
xcodebuild \
  -project MxxHub.xcodeproj \
  -scheme MxxHub \
  -configuration Release \
  -sdk iphoneos \
  -destination 'generic/platform=iOS' \
  CODE_SIGNING_ALLOWED=NO \
  CODE_SIGNING_REQUIRED=NO \
  CONFIGURATION_BUILD_DIR="$PWD/build" \
  build
rm -rf Payload MxxHub-v0.3-unsigned.ipa
mkdir Payload
cp -R build/MxxHub.app Payload/
zip -qry MxxHub-v0.3-unsigned.ipa Payload
rm -rf Payload
echo "Built: $PWD/MxxHub-v0.3-unsigned.ipa"
