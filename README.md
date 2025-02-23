About
-----
Based on YT-DLP, this script can be used to download Wrexham AFC fixture videos. It will default to the next upcoming Live event, but allows specifying an OnDemand video ID as well.

You must be logged into Chrome at https://wrexhamafc.co.uk and have a valid iFollow audio/video pass.

This script does not circumvent any subscription requirements. 

Usage
-----
```
bash wrexham-dl.sh [--id=live] [--audio] [--video]
```

Example:
```
bash wrexham-dl.sh
bash wrexham-dl.sh --id=live --audio
bash wrexham-dl.sh --id=live --video
```