# tvdash kiosk app (Samsung Tizen TV)

A tiny web app that shows one page full screen, without bars. The tvdash service starts it and then sends it the
address over a channel on the TV (`tv.kiosk_show`); a TV does not pass launch arguments to an app, see the
"Live web page" section of `CLAUDE.md` for what was tried.

Files: `config.xml` (app id `TvDash0001.TvDash`, name *tvdash companion*), `index.html` (the loading screen: logo, status line), `app.js`, `app.css`, `icon.png`. Tizen's content
security policy forbids inline script and style, so keep them in their own files.

## Install on a TV

1. Switch the TV to developer mode: Apps, type `12345`, Developer mode on, enter the IP of the machine you install from.
2. Install the Tizen Studio command line tools (`web-cli_Tizen_Studio_*_ubuntu-64.bin --accept-license ~/tizen-studio`),
   then with `package-manager-cli.bin install Certificate-Manager cert-add-on TV-SAMSUNG-Extension-Tools`
   (on Ubuntu that needs `libkf5itemmodels5 libkchart2`).
3. Run `~/tizen-studio/tools/certificate-manager/certificate-manager` and create a **Samsung** certificate profile for
   TV: a new author certificate, a distributor certificate with privilege level *Public*, sign in with your Samsung
   account and add the TV (it is listed once `sdb connect <tv>` has been run). The certificates and their passwords
   stay on your machine; never commit them.
4. Build and install, from a scratch copy because packaging writes into the directory:

```
export PATH=$PATH:~/tizen-studio/tools/ide/bin:~/tizen-studio/tools
sdb connect <tv>
cp -r tizen /tmp/tvdash-app && cd /tmp/tvdash-app
tizen package -t wgt -s <profile> -- .
mv "tvdash companion.wgt" tvdash-companion.wgt      # the TV refuses to install a package with a space in its file name
tizen install -n tvdash-companion.wgt -s <tv>:26101
```

Try it: `python tvdash.py show-url --live --host <tv> --url https://example.org/`, and `python tvdash.py hide --host <tv>`.

The page you open has to work in the TV's browser engine, Chromium 76 at 1920x1080 (no `clamp()`, `min()`,
`aspect-ratio`, `inset`, `dvh`, flexbox `gap`, `?.`, `??`).
