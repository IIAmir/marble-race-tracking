/* MarbleTrack UI language: English (as written in app.js) or Persian, right to left.
   The app renders English; in Persian every text node, tooltip, placeholder and unit is translated
   as it appears (a MutationObserver), including the messages that come from the engine. Sentences
   with bold or code inside are translated whole (BLOCK), so the Persian word order is natural.
   Anything not in the tables stays in English rather than being guessed. The choice is kept per
   PC (localStorage); the button in the top bar switches and reloads. */
"use strict";
(() => {
  const KEY = "marbletrack.lang";
  // The app passes the language chosen on this PC in the address (?lang=); the page's own storage is
  // only a fallback (it is per port, and the port changes from one start to the next).
  const params = new URLSearchParams(location.search);
  let lang = params.get("lang") || "";
  if (!lang) { try { lang = localStorage.getItem(KEY) || ""; } catch { /* storage blocked */ } }
  if (lang !== "en") lang = "fa";
  window.MT_LANG = lang;
  function addSwitch() {
    const right = document.querySelector(".bar-right");
    if (!right || document.getElementById("langBtn")) return;
    const b = document.createElement("button");
    b.id = "langBtn"; b.className = "btn sm ghost lang"; b.type = "button";
    b.textContent = lang === "fa" ? "English" : "فارسی";
    b.title = lang === "fa" ? "Switch the interface to English" : "رابط کاربری فارسی";
    b.setAttribute("data-noi18n", "");
    b.onclick = async () => {
      const next = lang === "fa" ? "en" : "fa";
      try { localStorage.setItem(KEY, next); } catch { /* ignore */ }
      try {  // remembered by the app for its next start
        await fetch("/api/set_language", { method: "POST", headers: { "X-Token": params.get("k") || "", "Content-Type": "application/json" }, body: JSON.stringify({ lang: next }) });
      } catch { /* the page still switches */ }
      params.set("lang", next);
      location.replace(`${location.pathname}?${params}`);
    };
    right.prepend(b);
  }
  if (lang !== "fa") { document.addEventListener("DOMContentLoaded", addSwitch); return; }

  document.documentElement.lang = "fa";
  document.documentElement.dir = "rtl";

  /* ------------------------------------------------------------ whole strings */
  const D = {
    // frame
    "MarbleTrack": "MarbleTrack", "Maps": "نقشه‌ها", "Set up all demo cameras": "راه‌اندازی خودکار دوربین‌های نمایشی",
    "Saved maps: open a route, start a new one, export or import": "نقشه‌های ذخیره‌شده: باز کردن یک مسیر، ساختن مسیر جدید، خروجی یا ورود",
    "Demo cameras: place waypoints, capture the empty track and learn the colours on every camera, as an installer would":
      "دوربین‌های نمایشی: نقاط مسیر، تصویر مسیر خالی و رنگ توپ‌ها را روی همهٔ دوربین‌ها، همان‌طور که نصاب انجام می‌دهد، آماده می‌کند",
    "Live": "زنده", "Setup": "راه‌اندازی", "Saving…": "در حال ذخیره…", "All changes saved": "همهٔ تغییرات ذخیره شد", "Not saved": "ذخیره نشد",
    "The app's engine is not responding. If this persists, restart MarbleTrack.": "موتور برنامه پاسخ نمی‌دهد. اگر ادامه داشت، MarbleTrack را دوباره اجرا کنید.",
    "Something went wrong": "مشکلی پیش آمد",
    "This page must be opened from the MarbleTrack app.": "این صفحه را باید از داخل برنامهٔ MarbleTrack باز کنید.",
    "Race & marbles": "مسابقه و توپ‌ها", "Cameras": "دوربین‌ها", "Track waypoints": "نقاط مسیر", "Empty track": "مسیر خالی",
    "Marble colours": "رنگ توپ‌ها", "Leaderboard output": "خروجی جدول", "Review & go live": "بازبینی و شروع زنده",
    "Demo cameras on": "دوربین‌های نمایشی فعال", "Not saved yet": "هنوز ذخیره نشده",
    "Back": "قبلی", "This step is complete.": "این مرحله کامل است.", "Everything is set up.": "همه چیز آماده است.",
    "Go live": "شروع زنده", "Continue": "ادامه", "Finish the steps marked above first": "اول مرحله‌های علامت‌خورده را کامل کنید",
    "Starting cameras…": "در حال روشن کردن دوربین‌ها…",
    "None found yet": "هنوز پیدا نشده", "No output": "بدون خروجی", "Address not set": "نشانی تعیین نشده", "Ready to go live": "آمادهٔ شروع زنده",
    "length not set": "طول تعیین نشده",
    // 1. race
    "Describe the track and the marbles once. Distances are measured along the track, from the start gate.":
      "مسیر و توپ‌ها را یک بار تعریف کنید. همهٔ فاصله‌ها در امتداد مسیر و از دروازهٔ شروع اندازه گرفته می‌شوند.",
    "Race name": "نام مسابقه", "Lap length": "طول هر دور", "Track length": "طول مسیر",
    "Start line, once round the circuit, back to the start line.": "از خط شروع، یک دور کامل، تا رسیدن دوباره به خط شروع.",
    "Start gate to the end of the run-out.": "از دروازهٔ شروع تا انتهای مسیر خروجی.",
    "Finish line at": "خط پایان در", "Distance from the start line, crossed on the last lap.": "فاصله از خط شروع؛ توپ‌ها در دور آخر از آن می‌گذرند.",
    "Distance from the start gate.": "فاصله از دروازهٔ شروع.", "Laps": "تعداد دور",
    "More than one: the track is a circuit that returns to the start line.": "بیش از یک: مسیر حلقه‌ای است که به خط شروع برمی‌گردد.",
    "Waypoint marks every": "فاصلهٔ علامت‌های مسیر", "Put a small mark on the track at this spacing.": "با این فاصله، علامت کوچکی روی مسیر بگذارید.",
    "More track settings": "تنظیمات بیشتر مسیر", "Race clock starts at": "شروع ساعت مسابقه در",
    "The clock starts when the first marble passes here.": "ساعت وقتی راه می‌افتد که اولین توپ از این نقطه بگذرد.",
    "Channel width": "عرض کانال", "Inside width of the running channel.": "عرض داخلی کانالی که توپ‌ها در آن حرکت می‌کنند.",
    "Marble diameter": "قطر توپ", "Tunnels and other places no camera can see": "تونل‌ها و جاهایی که هیچ دوربینی نمی‌بیند",
    "e.g. 3.6–4.0, 9.2–9.5": "مثلاً 3.6–4.0, 9.2–9.5",
    "Ranges in metres. The tracker holds positions there instead of guessing.": "بازه‌ها به متر. ردیاب در این بازه‌ها جای توپ‌ها را نگه می‌دارد و حدس نمی‌زند.",
    "Wider sections of the channel": "بخش‌های پهن‌تر کانال", "e.g. 6.5–7.0 @ 105, 15–15.7 @ 90": "مثلاً 6.5–7.0 @ 105, 15–15.7 @ 90",
    "From–to in metres @ inside width in mm, for obstacle zones and splitters wider than the channel width above. The search band follows them.":
      "از–تا به متر، سپس @ و عرض داخلی به میلی‌متر؛ برای منطقه‌های مانع و جداکننده‌هایی که از عرض کانال پهن‌ترند. نوار جست‌وجو از این عرض‌ها پیروی می‌کند.",
    "Start a new race automatically when marbles return after the track was clear": "وقتی مسیر خالی شد و توپ‌ها دوباره آمدند، مسابقهٔ جدید خودکار شروع شود",
    "Confirm the result by itself when the finish-rack camera agrees with the board": "اگر دوربین قفسهٔ پایان با جدول هم‌خوان بود، نتیجه خودکار تأیید شود",
    "Marbles": "توپ‌ها", "Add marble": "افزودن توپ", "Marble name": "نام توپ",
    "The colour swatch is for the leaderboard only. Each camera learns the real colours in step 5.": "این رنگ فقط برای نمایش در جدول است. هر دوربین رنگ واقعی توپ‌ها را در مرحلهٔ ۵ یاد می‌گیرد.",
    "Write ranges like 3.6–4.0, separated by commas.": "بازه‌ها را به شکل 3.6–4.0 بنویسید و با ویرگول (,) جدا کنید.",
    "Write sections like 6.5–7.0 @ 105 (metres, then width in mm), separated by commas.": "بخش‌ها را به شکل 6.5–7.0 @ 105 بنویسید (متر، سپس عرض به میلی‌متر) و با ویرگول (,) جدا کنید.",
    "Enter the track length.": "طول مسیر را وارد کنید.", "The race needs at least one lap.": "مسابقه دست‌کم یک دور لازم دارد.",
    "The finish line must be between the start and the end of the track.": "خط پایان باید بین شروع و انتهای مسیر باشد.",
    "Add at least two marbles.": "دست‌کم دو توپ اضافه کنید.", "Every marble needs a name.": "هر توپ باید نام داشته باشد.",
    // 2. cameras
    "Connect every camera, then put them in order along the track, from the start gate to the finish. The finish camera gets its own role.":
      "همهٔ دوربین‌ها را وصل کنید، سپس آن‌ها را به ترتیب طول مسیر، از دروازهٔ شروع تا خط پایان، مرتب کنید. دوربین خط پایان نقش جداگانه‌ای دارد.",
    "Find cameras": "یافتن دوربین‌ها", "Looking for cameras…": "در حال جست‌وجوی دوربین‌ها…", "No cameras found": "هیچ دوربینی پیدا نشد",
    "MarbleTrack looks for NDI cameras on the network, USB cameras and industrial (Basler) cameras.": "MarbleTrack دوربین‌های NDI روی شبکه، دوربین‌های USB و دوربین‌های صنعتی (Basler) را جست‌وجو می‌کند.",
    "Check the cable and that the camera has power.": "کابل و برق دوربین را بررسی کنید.",
    "NDI cameras: the PC and the cameras must be on the same network, and Windows Firewall must allow MarbleTrack (the installer adds this rule; if Windows asked, tick both Private and Public).":
      "دوربین‌های NDI: رایانه و دوربین‌ها باید در یک شبکه باشند و فایروال ویندوز باید به MarbleTrack اجازه بدهد (برنامهٔ نصب این اجازه را اضافه می‌کند؛ اگر ویندوز پرسید، هر دو گزینهٔ Private و Public را تیک بزنید).",
    "Close other apps that use the camera (Teams, Zoom, the Camera app).": "برنامه‌های دیگری را که از دوربین استفاده می‌کنند (Teams، Zoom، برنامهٔ Camera) ببندید.",
    "Many cameras on one USB controller can exceed its bandwidth: spread them over ports or add a USB card.": "چند دوربین روی یک کنترلر USB ممکن است از پهنای باند آن بیشتر شوند: آن‌ها را روی پورت‌های مختلف پخش کنید یا کارت USB اضافه کنید.",
    "Basler cameras need the pylon software installed.": "دوربین‌های Basler به نرم‌افزار pylon نیاز دارند.",
    "Finish": "خط پایان", "Finish rack": "قفسهٔ پایان", "Not used": "استفاده نمی‌شود", "Not connected.": "وصل نیست.",
    "Plug it in, then Find cameras.": "وصلش کنید، سپس «یافتن دوربین‌ها» را بزنید.", "Error": "خطا", "Use this camera": "استفاده از این دوربین",
    "Earlier on the track": "نزدیک‌تر به شروع مسیر", "Later on the track": "نزدیک‌تر به پایان مسیر", "Move earlier": "جابه‌جایی به عقب", "Move later": "جابه‌جایی به جلو",
    "Watches a track section": "یک بخش از مسیر را می‌بیند", "Watches the finish line": "خط پایان را می‌بیند", "Watches the finish rack": "قفسهٔ پایان را می‌بیند",
    "What this camera watches": "این دوربین چه چیزی را می‌بیند", "Exposure & colour": "نوردهی و رنگ",
    "NDI cameras keep the exposure, shutter and white balance set in their own web page; the switches below do not change them.":
      "تنظیمات نوردهی، شاتر و تراز سفیدی دوربین‌های NDI در صفحهٔ وب خود دوربین انجام می‌شود؛ کلیدهای زیر آن‌ها را تغییر نمی‌دهند.",
    "Automatic exposure": "نوردهی خودکار", "Exposure": "نوردهی",
    "Short exposures stop motion blur. Keep the lighting constant.": "نوردهی کوتاه جلوی تاری حرکت را می‌گیرد. نور را ثابت نگه دارید.",
    "Automatic white balance": "تراز سفیدی خودکار",
    "Lock both for racing: automatic changes shift the marble colours the tracker learned.": "برای مسابقه هر دو را خاموش کنید: تغییر خودکار، رنگ‌هایی را که ردیاب یاد گرفته جابه‌جا می‌کند.",
    "Camera delay": "تأخیر دوربین", "Time from exposure to the PC. USB webcams: about 40 ms.": "زمان از لحظهٔ ثبت تصویر تا رسیدن آن به رایانه. وب‌کم‌های USB: حدود ۴۰ میلی‌ثانیه.",
    "Sensor readout": "زمان خواندن حسگر",
    "Rolling-shutter cameras expose the rows one after another; each marble is timed by its own row. From the camera's data sheet, or measured; 0 for a global shutter.":
      "دوربین‌های رولینگ‌شاتر ردیف‌های تصویر را یکی پس از دیگری ثبت می‌کنند؛ زمان هر توپ از ردیف خودش حساب می‌شود. از دیتاشیت دوربین یا با اندازه‌گیری؛ برای شاتر سراسری 0.",
    "Remove from project": "حذف از پروژه", "Camera name": "نام دوربین", "Demo": "نمایشی", "NDI": "NDI", "USB": "USB", "Basler": "Basler",
    "Turn on at least one camera.": "دست‌کم یک دوربین را روشن کنید.",
    "No image: check the camera": "تصویری نیست: دوربین را بررسی کنید",
    // shared viewer, coverage
    "Whole track": "کل مسیر", "Track coverage": "پوشش مسیر",
    // 3. waypoints
    "In each camera, click the distance marks along the track in race direction. They tell the tracker how far along the track every pixel is, perspective included.":
      "در هر دوربین، روی علامت‌های فاصله در طول مسیر، در جهت حرکت مسابقه، کلیک کنید. این نقطه‌ها به ردیاب می‌گویند هر پیکسل تصویر چقدر از ابتدای مسیر فاصله دارد، با در نظر گرفتن پرسپکتیو.",
    "Coverage of the whole track": "پوشش کل مسیر",
    "Each bar is one camera. Hatched red stretches are seen by no camera: the tracker treats them like a tunnel.":
      "هر نوار یک دوربین است. تکه‌های هاشورخوردهٔ قرمز را هیچ دوربینی نمی‌بیند و ردیاب با آن‌ها مثل تونل رفتار می‌کند.",
    "Turn on at least one camera in step 2 first.": "اول در مرحلهٔ ۲ دست‌کم یک دوربین را روشن کنید.",
    "Now the next mark in race direction.": "حالا علامت بعدی در جهت حرکت.",
    "Waypoints": "نقاط مسیر", "Covers": "پوشش", "Spacing": "فاصله", "Detail": "جزئیات",
    "Track length per image pixel along the track: what one pixel of error costs": "طول مسیر به ازای هر پیکسل تصویر: یک پیکسل خطا چقدر خطای مکان می‌دهد",
    "Track band width": "پهنای نوار جست‌وجو",
    "The blue band is where the tracker looks for marbles. It should cover the channel, walls included.": "نوار آبی جایی است که ردیاب در آن دنبال توپ می‌گردد. باید کل کانال را، همراه با دیواره‌ها، بپوشاند.",
    "Undo": "برگرداندن", "New stretch": "تکهٔ جدید", "Clear all": "پاک کردن همه", "Place for me (demo)": "چیدن خودکار (نمایشی)",
    "Places the marks an installer would click": "همان نقطه‌هایی را می‌گذارد که نصاب کلیک می‌کرد", "Placing…": "در حال چیدن…",
    "This camera also sees another part of the track (the other leg of a hairpin, a pass under a bridge): mark it as a separate stretch, in race order":
      "این دوربین بخش دیگری از مسیر را هم می‌بیند (بازوی دیگر یک پیچ تند، یا عبوری زیر پل): آن را به‌صورت یک تکهٔ جدا، به ترتیب مسابقه، علامت بزنید",
    "Drag a waypoint to move it. Click its label to change its distance.": "برای جابه‌جایی، نقطه را بکشید. برای تغییر فاصله، روی برچسبش کلیک کنید.",
    "Moving the first waypoint shifts all of them.": "تغییر فاصلهٔ نقطهٔ اول، همهٔ نقطه‌ها را جابه‌جا می‌کند.", "Only this waypoint changes.": "فقط همین نقطه تغییر می‌کند.",
    "Cancel": "انصراف", "Save": "ذخیره",
    "click inside the image": "داخل تصویر کلیک کنید", "that is on top of the previous waypoint": "این نقطه روی نقطهٔ قبلی افتاده است",
    "the current stretch needs at least two waypoints": "تکهٔ فعلی دست‌کم دو نقطه لازم دارد",
    "place at least two waypoints on the first stretch first": "اول دست‌کم دو نقطه روی تکهٔ اول بگذارید",
    "place this camera's waypoints first (step 3)": "اول نقاط مسیر این دوربین را بگذارید (مرحلهٔ ۳)",
    "pick a marble first": "اول یک توپ انتخاب کنید", "no background yet": "هنوز تصویر مسیر خالی گرفته نشده",
    "stop tracking first (Back to setup)": "اول ردیابی را متوقف کنید (بازگشت به راه‌اندازی)", "values must be positive numbers": "مقدارها باید عدد مثبت باشند",
    "that map no longer exists": "این نقشه دیگر وجود ندارد", "that camera is no longer in the project": "این دوربین دیگر در پروژه نیست",
    "no such waypoint": "چنین نقطه‌ای وجود ندارد", "unknown marble": "توپ ناشناخته", "not live": "در حالت زنده نیست",
    "only with demo cameras (start with --demo)": "فقط با دوربین‌های نمایشی (اجرا با --demo)",
    "Row fields need {n} where the row number goes, e.g. P{n}Name.Text": "فیلدهای ردیف باید {n} را به‌جای شمارهٔ ردیف داشته باشند، مثلاً P{n}Name.Text",
    // 4. background
    "Each camera stores what the empty track looks like, so marbles stand out. Do this with the race lighting on.":
      "هر دوربین تصویر مسیر خالی را ذخیره می‌کند تا توپ‌ها از زمینه جدا دیده شوند. این کار را با نور مسابقه انجام دهید.",
    "Capture all cameras": "عکس‌برداری از همهٔ دوربین‌ها", "Capturing…": "در حال عکس‌برداری…", "Failed": "ناموفق", "Something moved": "چیزی حرکت کرد",
    "Captured": "گرفته شد", "Not yet": "هنوز نه", "No capture yet": "هنوز تصویری گرفته نشده", "Again": "دوباره",
    // 5. colours
    "Each camera learns every marble's colour under its own light. Pick a marble on the right, then click it in the picture.":
      "هر دوربین رنگ هر توپ را زیر نور خودش یاد می‌گیرد. یک توپ را از فهرست کنار تصویر انتخاب کنید، سپس روی همان توپ در تصویر کلیک کنید.",
    "Pick a marble in the list first.": "اول یک توپ را از فهرست انتخاب کنید.",
    "Place this camera's waypoints first (step 3).": "اول نقاط مسیر این دوربین را بگذارید (مرحلهٔ ۳).",
    "Sample for me (demo)": "نمونه‌برداری خودکار (نمایشی)", "Sampling…": "در حال نمونه‌برداری…",
    "Capture the empty track first (step 4): clicks work much better with it.": "اول تصویر مسیر خالی را بگیرید (مرحلهٔ ۴): کلیک‌ها با آن خیلی بهتر کار می‌کنند.",
    "Demo: the marbles are parked for you. A short view shows them in groups that take turns.": "نمایشی: توپ‌ها برایتان پارک شده‌اند. در نماهای کوتاه، توپ‌ها گروه‌گروه و به نوبت دیده می‌شوند.",
    "Put the marbles on the track where this camera sees them, not touching each other.": "توپ‌ها را جایی از مسیر بگذارید که این دوربین می‌بیند، طوری که به هم نچسبند.",
    "To do": "مانده", "Copied": "کپی‌شده", "Copied from another camera": "از دوربین دیگری کپی شده",
    "Every marble is clearly distinguishable on this camera.": "همهٔ توپ‌ها در این دوربین به‌خوبی از هم تشخیص داده می‌شوند.",
    "Can't place every marble in view?": "نمی‌توانید همهٔ توپ‌ها را جلوی این دوربین بگذارید؟", "Copy missing colours from…": "کپی رنگ‌های جاافتاده از…",
    "A fallback: lighting differs between cameras, so samples taken here are more reliable.": "راه جایگزین: نور دوربین‌ها با هم فرق دارد، پس نمونه‌برداری در همین دوربین قابل اعتمادتر است.",
    // 6. output
    "MarbleTrack fills a vMix title with the live leaderboard, and can also send it to your backend over a WebSocket, about ten times a second, plus an event for every start, finish and camera problem.":
      "MarbleTrack جدول زنده را در یک تایتل vMix می‌نویسد و می‌تواند آن را از طریق WebSocket، حدود ده بار در ثانیه، برای سرور شما هم بفرستد؛ به‌علاوهٔ یک رویداد برای هر شروع، پایان و مشکل دوربین.",
    "Fill a vMix title": "پر کردن تایتل vMix", "vMix computer": "رایانهٔ vMix", "Title input": "ورودی تایتل",
    "The title's name or number in vMix.": "نام یا شمارهٔ تایتل در vMix.", "Name field": "فیلد نام", "{n} is the row: P1Name.Text, P2Name.Text, …": "{n} شمارهٔ ردیف است: P1Name.Text، P2Name.Text، …",
    "Time / gap field": "فیلد زمان / فاصله", "Finish time, or the gap to the leader while racing.": "زمان پایان، یا در طول مسابقه فاصله تا نفر اول.",
    "Colour swatch": "نمونهٔ رنگ", "e.g. P{n}Color.Fill.Color": "مثلاً P{n}Color.Fill.Color", "A shape filled with the marble's colour. Optional.": "شکلی که با رنگ توپ پر می‌شود. اختیاری.",
    "Clock and status fields": "فیلدهای ساعت و وضعیت", "Race clock; READY / LIVE / FINAL. Leave empty to skip.": "ساعت مسابقه؛ READY / LIVE / FINAL. برای استفاده نکردن خالی بگذارید.",
    "Rows in the title": "تعداد ردیف‌های تایتل", "0 = one row per marble.": "0 = یک ردیف برای هر توپ.", "Lap field": "فیلد دور", "e.g. Lap.Text": "مثلاً Lap.Text",
    "Lap races: LAP 2/3, then FINAL LAP. Optional.": "مسابقه‌های چنددوری: LAP 2/3 و سپس FINAL LAP. اختیاری.",
    "Test vMix": "آزمایش vMix", "Connecting…": "در حال اتصال…", "Stream to a server": "ارسال به سرور", "Server address": "نشانی سرور",
    "Starts with ws:// or wss:// (secure).": "با ws:// یا wss:// (امن) شروع می‌شود.", "Access token": "توکن دسترسی", "Optional": "اختیاری",
    "Show": "نمایش", "Hide": "پنهان", "Station name": "نام ایستگاه", "Tells your backend which track this is.": "به سرور می‌گوید این کدام مسیر است.",
    "Updates per second": "به‌روزرسانی در ثانیه", "Test connection": "آزمایش اتصال", "Record every camera while live": "ضبط همهٔ دوربین‌ها در حالت زنده",
    "Recordings folder": "پوشهٔ ضبط", "recordings, inside this map's folder": "recordings، داخل پوشهٔ همین نقشه", "What gets sent": "چه چیزی فرستاده می‌شود",
    "Connected": "وصل", "Reconnecting…": "در حال اتصال دوباره…", "Not connected": "وصل نیست", "Off": "خاموش", "Stopped": "متوقف",
    "Streaming is turned off": "ارسال به سرور خاموش است", "vMix output is turned off": "خروجی vMix خاموش است",
    // 7. review
    "Check everything, then go live. You can come back to setup at any time.": "همه چیز را بررسی کنید، سپس زنده شوید. هر وقت خواستید می‌توانید به راه‌اندازی برگردید.",
    "Fix": "رفع", "Coverage": "پوشش", "Camera views": "نمای دوربین‌ها", "Check camera views": "بررسی نمای دوربین‌ها", "Comparing…": "در حال مقایسه…",
    "Compares what every camera sees now with the empty track saved in this map. Do this whenever you reopen a map: cameras may have been moved since.":
      "تصویر فعلی هر دوربین را با تصویر مسیر خالیِ ذخیره‌شده در این نقشه مقایسه می‌کند. هر بار که نقشه‌ای را دوباره باز می‌کنید این کار را بکنید؛ ممکن است دوربین‌ها از آن زمان جابه‌جا شده باشند.",
    "No finish-line camera. Finish times then come from the section camera at the line (a few ms resolution instead of under one).":
      "دوربین خط پایان تعریف نشده. زمان پایان از دوربین بخشی که خط را می‌بیند گرفته می‌شود (دقت چند میلی‌ثانیه به‌جای کمتر از یک میلی‌ثانیه).",
    "Matches the map": "با نقشه یکی است", "Moved": "جابه‌جا شده", "Different view": "نمای متفاوت", "Different resolution": "رزولوشن متفاوت",
    "Waypoints carried over": "نقاط منتقل شد", "Redo": "باید دوباره انجام شود", "No picture": "بدون تصویر", "Carry waypoints over": "انتقال نقاط مسیر",
    "Redo waypoints": "انجام دوبارهٔ نقاط مسیر", "Capture empty track": "گرفتن تصویر مسیر خالی",
    // live
    "Waiting": "در انتظار", "New race": "مسابقهٔ جدید", "Clear the board and wait for the next race": "جدول را پاک کن و منتظر مسابقهٔ بعدی بمان",
    "Back to setup": "بازگشت به راه‌اندازی", "Confirm result": "تأیید نتیجه", "Result confirmed ✓": "نتیجه تأیید شد ✓",
    "The result goes out as PROVISIONAL until you confirm it (after checking the finish rack)": "نتیجه تا وقتی شما تأییدش نکنید (پس از نگاه به قفسهٔ پایان) «موقت» پخش می‌شود",
    "Freeze board": "ثابت کردن جدول", "Unfreeze board": "آزاد کردن جدول", "Keep the board on air exactly as it is now": "جدول را دقیقاً همین‌طور که هست روی آنتن نگه دار",
    "Hide board": "پنهان کردن جدول", "Show board": "نمایش جدول", "Take the board off air (vMix fields emptied)": "جدول را از آنتن بردار (فیلدهای vMix خالی می‌شوند)",
    "Click a marble to swap it with another or to move it in the result. Every correction is logged.": "روی یک توپ کلیک کنید تا آن را با توپ دیگری جابه‌جا کنید یا جایش را در نتیجه تغییر دهید. هر اصلاح ثبت می‌شود.",
    "Training data": "دادهٔ آموزشی", "No marble (empty track)": "بدون توپ (مسیر خالی)", "Start": "شروع", "Stop": "توقف",
    "Waiting for marbles": "در انتظار توپ‌ها", "Racing": "در حال مسابقه", "Finished": "تمام شد", "Final": "نهایی", "Provisional": "موقت",
    "Tracking started after this race had begun: the clock and finish times are estimates.": "ردیابی بعد از شروع این مسابقه آغاز شد: ساعت و زمان‌های پایان تخمینی‌اند.",
    "not seen yet": "هنوز دیده نشده", "photo finish: review": "فوتوفینیش: بازبینی شود", "hidden": "پنهان", "lost": "گم‌شده", "new": "تازه",
    "The tracker has it mixed up with": "ردیاب آن را با این توپ قاطی کرده", "Swap the two": "جابه‌جا کردن این دو", "Move up": "یک رتبه بالاتر", "Move down": "یک رتبه پایین‌تر",
    "Leaderboard server": "سرور جدول", "vMix": "vMix", "Race control": "کنترل مسابقه", "starting…": "در حال شروع…",
    "Race started": "مسابقه شروع شد", "Race complete": "مسابقه تمام شد", "Stopping…": "در حال توقف…",
    "A race is running. Stop tracking and go back to setup?": "مسابقه در جریان است. ردیابی متوقف شود و به راه‌اندازی برگردید؟",
    "Finish rack agrees with the board": "قفسهٔ پایان با جدول هم‌خوان است", "Finish rack agrees with the board: result confirmed": "قفسهٔ پایان با جدول هم‌خوان است: نتیجه تأیید شد",
    // maps
    "One map per route: cameras, waypoints, empty-track images and colours. Every map is kept, so an old route can be reopened later. When you reopen one, check the camera views on the Review step.":
      "برای هر مسیر یک نقشه: دوربین‌ها، نقاط مسیر، تصویرهای مسیر خالی و رنگ‌ها. همهٔ نقشه‌ها نگه داشته می‌شوند تا هر مسیر قدیمی را بعداً دوباره باز کنید. وقتی نقشه‌ای را دوباره باز می‌کنید، در مرحلهٔ «بازبینی» نمای دوربین‌ها را بررسی کنید.",
    "This map": "این نقشه", "Name": "نام", "Saved": "ذخیره‌شده", "Notes": "یادداشت‌ها", "Which draw or route this is, what changed…": "کدام قرعه یا مسیر است، چه چیزی تغییر کرده…",
    "Export as .zip": "خروجی گرفتن (zip)", "New route": "مسیر جدید", "Name of the new route": "نام مسیر جدید", "Start new route": "ساختن مسیر جدید",
    "Import .zip": "ورود فایل zip", "open": "باز", "never saved": "هرگز ذخیره نشده", "Open": "باز کردن", "Copy as new route": "کپی به‌عنوان مسیر جدید",
    "Export": "خروجی", "No saved maps yet.": "هنوز نقشه‌ای ذخیره نشده.", "Close": "بستن", "Give the new route a name first.": "اول برای مسیر جدید نام بگذارید.",
    "Creating…": "در حال ساخت…", "Opening…": "در حال باز کردن…", "Copying…": "در حال کپی…", "Exporting…": "در حال خروجی گرفتن…",
    "Name of the new route (a copy of this map):": "نام مسیر جدید (کپی این نقشه):", "Comparing every camera with the saved map…": "در حال مقایسهٔ همهٔ دوربین‌ها با نقشهٔ ذخیره‌شده…",
    "Import failed": "ورود ناموفق بود", "Setting up…": "در حال راه‌اندازی…", "…": "…",
    "Enter the server address, or turn off \"Stream to a server\" (step 6) if you only use vMix.": "نشانی سرور را وارد کنید، یا اگر فقط از vMix استفاده می‌کنید «ارسال به سرور» را (مرحلهٔ ۶) خاموش کنید.",
    "The address must start with ws:// or wss://": "نشانی باید با ws:// یا wss:// شروع شود",
    "The server refused the token (HTTP 401/403). Check the access token.": "سرور توکن را نپذیرفت (HTTP 401/403). توکن دسترسی را بررسی کنید.",
    "Nothing is listening at that address (connection refused). Is the server running?": "در آن نشانی چیزی در حال گوش دادن نیست (اتصال رد شد). آیا سرور روشن است؟",
    "No answer within 5 s. Check the address, the network and the firewall.": "در ۵ ثانیه پاسخی نیامد. نشانی، شبکه و فایروال را بررسی کنید.",
    "The server name could not be found (DNS). Check the address.": "نام سرور پیدا نشد (DNS). نشانی را بررسی کنید.",
    "Ctrl+Z": "Ctrl+Z", "connecting to the camera…": "در حال اتصال به دوربین…", "a new race has started since: look at the board again": "از آن موقع مسابقهٔ جدیدی شروع شده: دوباره به جدول نگاه کنید", "Guide": "راهنما", "Open the user guide (PDF)": "باز کردن راهنمای کاربر (PDF)",
    "the guide is not included in this build": "راهنما در این نسخه از برنامه نیست",
  };
  /* ------------------------------------------------------------ patterns (applied in order to strings not found above) */
  const R = [
    [/^Saved in (.+)$/, "ذخیره در $1"], [/^Version (.+)$/, "نسخهٔ $1"],
    [/^(\d+) of (\d+) cameras? in use$/, "$1 از $2 دوربین در حال استفاده"], [/^(\d+) of (\d+) cameras? placed$/, "$1 از $2 دوربین آماده"],
    [/^(\d+) of (\d+) captured$/, "$1 از $2 گرفته شده"], [/^(\d+) of (\d+) cameras? complete$/, "$1 از $2 دوربین کامل"],
    [/^(\d+) things? left$/, "$1 مورد مانده"], [/^(\d+) of (\d+) marbles? learned$/, "$1 از $2 توپ یاد گرفته شد"],
    [/^(\d+) of (\d+) online$/, "$1 از $2 آنلاین"],
    [/^(\d+)–(\d+) fps · (\d+) ms per frame \(slowest\)$/, "$1–$2 fps · $3 میلی‌ثانیه برای هر فریم (کندترین)"],
    [/^([\d,]+) messages? sent$/, "$1 پیام فرستاده شد"], [/^([\d,]+) field updates?$/, "$1 به‌روزرسانی فیلد"], [/^(\d+) rejected · /, "$1 مورد رد شد · "],
    [/^(\d+) cameras? connected\.$/, "$1 دوربین وصل است."], [/^Section (\d+)$/, "بخش $1"], [/^Waypoint (\d+) is at$/, "نقطهٔ $1 در"],
    [/^Remove (.+)\? Its colour samples on every camera are deleted too\.$/, "$1 حذف شود؟ نمونه‌های رنگ آن در همهٔ دوربین‌ها هم پاک می‌شوند."],
    [/^Remove all (\d+) waypoints of (.+)\?$/, "همهٔ $1 نقطهٔ مسیر $2 پاک شود؟"],
    [/^Remove (.+)$/, "حذف $1"], [/^Colour of (.+)$/, "رنگ $1"], [/^Capture (.+) again$/, "عکس‌برداری دوباره از $1"],
    [/^Forget (.+) on this camera$/, "حذف نمونهٔ $1 در این دوربین"], [/^(.+) alone$/, "فقط $1"],
    [/^Saved maps \((\d+)\)$/, "نقشه‌های ذخیره‌شده ($1)"], [/^Carry all over \((\d+)\)$/, "انتقال همه ($1)"],
    [/^Saved to (.+)$/, "ذخیره شد در $1"], [/^Exported to (.+)$/, "خروجی ذخیره شد در $1"], [/^Imported (.+)$/, "وارد شد: $1"],
    [/^Captured (\d+) cameras?\.$/, "از $1 دوربین عکس گرفته شد."],
    [/^(\d+) cameras? could not be captured\. See the cards below\.$/, "از $1 دوربین نشد عکس گرفت. کارت‌های پایین را ببینید."],
    [/^(\d+)% of the track changed while capturing$/, "$1٪ از مسیر هنگام عکس‌برداری تغییر کرد"],
    [/^(\d+) demo cameras? set up: waypoints, empty track and colours\. Review, then Go live\.$/, "$1 دوربین نمایشی آماده شد: نقاط مسیر، مسیر خالی و رنگ‌ها. بازبینی کنید، سپس «شروع زنده» را بزنید."],
    [/^All (\d+) camera views match this map\.$/, "نمای هر $1 دوربین با این نقشه یکی است."],
    [/^Waypoints carried over for (.+)\. Check them in step 3, then capture the empty track again \(step 4\)\.$/, "نقاط مسیر برای $1 منتقل شد. آن‌ها را در مرحلهٔ ۳ بررسی کنید و سپس تصویر مسیر خالی را دوباره بگیرید (مرحلهٔ ۴)."],
    [/^Something moved while capturing (.+)\. Capture again once the track is still\.$/, "هنگام عکس‌برداری از $1 چیزی حرکت کرد. وقتی مسیر ساکن شد، دوباره عکس بگیرید."],
    [/^Copied (\d+) colours?\. Copied colours are a fallback: sampling on this camera is more reliable\.$/, "$1 رنگ کپی شد. رنگ کپی‌شده راه جایگزین است؛ نمونه‌برداری در همین دوربین قابل اعتمادتر است."],
    [/^Finish rack and board disagree: (.+)\. Check before confirming the result\.$/, "قفسهٔ پایان و جدول با هم نمی‌خوانند: $1. پیش از تأیید نتیجه بررسی کنید."],
    [/^(.+) looks like (.+) to this camera\. Click (.+) again in another spot, or see the advice on the right\.$/, "این دوربین $1 را شبیه $2 می‌بیند. دوباره روی $3 در جای دیگری کلیک کنید یا توصیهٔ کنار تصویر را ببینید."],
    [/^(?:One click|(\d+) clicks) of (.+) looked different from the others and (?:is|are) left out \(part of the marble may blend into the track there\)\. Click (.+) where it stands out clearly\.$/,
      "یک یا چند کلیک روی $2 با بقیه فرق داشت و کنار گذاشته شد (شاید بخشی از توپ آن‌جا با مسیر هم‌رنگ شده باشد). روی $3 جایی کلیک کنید که به‌وضوح دیده می‌شود."],
    [/^not enough pixels to learn this colour \((.+)\); move closer or click a bigger marble$/, "پیکسل کافی برای یاد گرفتن این رنگ نیست؛ دوربین را نزدیک‌تر کنید یا روی توپ بزرگ‌تری کلیک کنید"],
    [/^must be more than (.+) m \(the previous waypoint\)$/, "باید بیشتر از $1 متر باشد (نقطهٔ قبلی)"], [/^must be less than (.+) m \(the next waypoint\)$/, "باید کمتر از $1 متر باشد (نقطهٔ بعدی)"],
    [/^Two marbles are called (.+): give each its own name\.$/, "دو توپ نام «$1» دارند: به هر کدام نام جداگانه بدهید."], [/^Names used twice: (.+)\.$/, "نام‌های تکراری: $1."],
    [/^(.+) is not connected\.$/, "$1 وصل نیست."],
    [/^(.+) now runs at (.+) instead of (.+): redo its waypoints and background\.$/, "$1 حالا با $2 به‌جای $3 کار می‌کند: نقاط مسیر و تصویر مسیر خالی را دوباره انجام دهید."],
    [/^(.+): the driver ignored some settings \((.+)\)\.$/, "$1: درایور بعضی تنظیمات را نادیده گرفت ($2)."],
    // step issues (also in the footer, with "(+N more)")
    [/: place at least two waypoints\./, ": دست‌کم دو نقطهٔ مسیر بگذارید."],
    [/ looks at a different view than in this map: redo its waypoints\./, " نمای متفاوتی با این نقشه دارد: نقاط مسیرش را دوباره بگذارید."],
    [/ has moved since this map was saved: carry its waypoints over \(Review\)\./, " از زمان ذخیرهٔ این نقشه جابه‌جا شده: نقاط مسیرش را منتقل کنید (مرحلهٔ بازبینی)."],
    [/: capture the empty track again \(the camera's view changed\)\./, ": تصویر مسیر خالی را دوباره بگیرید (نمای دوربین تغییر کرده)."],
    [/: capture the empty track\./, ": تصویر مسیر خالی را بگیرید."],
    [/: (\d+) marbles? without a colour sample\./, ": $1 توپ بدون نمونهٔ رنگ."],
    [/ \(\+(\d+) more\)$/, " (و $1 مورد دیگر)"],
    // review
    [/^No camera's waypoints reach 5 cm past the finish line \((.+)\)\. Marbles get a finish time only from sightings on both sides of the line: add a waypoint after the line on the finish camera\.$/,
      "نقاط مسیر هیچ دوربینی تا ۵ سانتی‌متر بعد از خط پایان ($1) نمی‌رسد. زمان پایان فقط وقتی ثبت می‌شود که توپ در دو طرف خط دیده شود: روی دوربین خط پایان، یک نقطه هم بعد از خط بگذارید."],
    [/^Not covered by any camera: (.+)\. The tracker holds positions there\.$/, "این بازه‌ها را هیچ دوربینی پوشش نمی‌دهد: $1. ردیاب در آن‌جا جای توپ‌ها را نگه می‌دارد."],
    [/^Not covered: (.+) to (.+)$/, "پوشش داده نشده: $1 تا $2"], [/^Tunnel \/ blind zone (.+) to (.+)$/, "تونل / بخش کور: $1 تا $2"], [/^Finish line (.+)$/, "خط پایان: $1"],
    [/^(\d+) stretch(?:es)? of track (?:is|are) not covered by any camera\. Fine for tunnels; otherwise move a camera or add waypoints\.$/,
      "$1 تکه از مسیر را هیچ دوربینی پوشش نمی‌دهد. برای تونل اشکالی ندارد؛ وگرنه یک دوربین را جابه‌جا کنید یا نقطهٔ مسیر اضافه کنید."],
    [/^([\d.]+)–([\d.]+) mm per pixel$/, "$1–$2 میلی‌متر در هر پیکسل"],
    // vMix / server tests
    [/^127\.0\.0\.1 when vMix runs on this PC\. Uses the vMix TCP API \(port (\d+)\)\.$/, "اگر vMix روی همین رایانه است: 127.0.0.1. از TCP API ‏vMix (پورت $1) استفاده می‌شود."],
    [/^vMix (.+), title (.+) found, connected in (\d+) ms$/, "vMix $1: تایتل $2 پیدا شد؛ اتصال در $3 میلی‌ثانیه"],
    [/^vMix (.+) is running but has no input called (.+)\.$/, "vMix $1 در حال اجراست ولی ورودی‌ای به نام $2 ندارد."],
    [/^Connected in (\d+) ms$/, "اتصال در $1 میلی‌ثانیه برقرار شد"],
    [/^Connected in (\d+) ms \(could not read the input list\)$/, "اتصال در $1 میلی‌ثانیه برقرار شد (فهرست ورودی‌ها خوانده نشد)"],
    [/^Nothing is listening at (\S+)\. Is vMix running\? \(Settings → Web Controller\/TCP API must be enabled; the TCP API uses port 8099\.\)/,
      "در نشانی $1 چیزی در حال گوش دادن نیست. آیا vMix باز است؟ (در vMix از Settings ← Web Controller/TCP API را فعال کنید؛ TCP API از پورت 8099 استفاده می‌کند.)"],
    [/^vMix at (\S+) did not answer within 3 s\.$/, "vMix در $1 در ۳ ثانیه پاسخ نداد."], [/^The address (.+) could not be found\.$/, "نشانی $1 پیدا نشد."],
    [/^vMix (.+): title (.+) has no field (\S+) \((\d+) missing\)\. Its fields: (.+)$/, "vMix $1: تایتل $2 فیلد $3 را ندارد ($4 فیلد کم است). فیلدهای آن: $5"],
    [/^Unexpected answer from vMix: (.+)$/, "پاسخ غیرمنتظره از vMix: $1"], [/^Secure connection failed: (.+)$/, "اتصال امن برقرار نشد: $1"],
    [/^The server answered but did not accept a WebSocket: (.+)$/, "سرور پاسخ داد ولی اتصال WebSocket را نپذیرفت: $1"],
    [/^Connecting to vMix at (\S+)$/, "در حال اتصال به vMix در $1"], [/^Connecting to (\S+)$/, "در حال اتصال به $1"], [/^Filling title (.+)$/, "در حال پر کردن تایتل $1"],
    [/ Retrying in (\d+) s\.$/, " تلاش دوباره تا $1 ثانیهٔ دیگر."],
    // live
    [/^Racing · joined late$/, "در حال مسابقه · دیر پیوست"], [/ · joined late$/, " · دیر پیوست"], [/ · off air$/, " · خارج از آنتن"], [/ · frozen$/, " · ثابت‌شده"],
    [/^(Final|Provisional)(?= ·|$)/, (m) => (m === "Final" ? "نهایی" : "موقت")],
    [/^Racing(?= ·)/, "در حال مسابقه"], [/^Waiting for marbles(?= ·)/, "در انتظار توپ‌ها"], [/^Finished(?= ·)/, "تمام شد"],
    [/^Lap (\d+)\/(\d+) · /, "دور $1/$2 · "], [/ · hidden$/, " · پنهان"], [/ · lost$/, " · گم‌شده"], [/ · new$/, " · تازه"], [/ · photo finish: review$/, " · فوتوفینیش: بازبینی شود"],
    [/^Saving crops of the empty track…$/, "در حال ذخیرهٔ تصویرهای مسیر خالی…"], [/^Saving crops of (.+)…$/, "در حال ذخیرهٔ تصویرهای $1…"],
    [/^(\d+) crops saved in the map's training folder$/, "$1 تصویر در پوشهٔ training نقشه ذخیره شد"],
    [/^(.+) finishes P(\d+) · (.+)$/, "$1 با رتبهٔ $2 تمام کرد · $3"], [/^New race \((.+)\)$/, (_, r) => `مسابقهٔ جدید (${{ operator: "اپراتور", "marbles back on the track": "توپ‌ها دوباره روی مسیر" }[r] || r})`],
    [/^(.+) stopped sending frames$/, "$1 دیگر تصویر نمی‌فرستد"], [/^(.+) back online$/, "$1 دوباره آنلاین شد"],
    [/^Operator: swapped (.+) and (.+)$/, "اپراتور: $1 و $2 را جابه‌جا کرد"], [/^Operator: set the order (.+)$/, "اپراتور: ترتیب را تعیین کرد: $1"],
    [/^Operator: confirmed the result$/, "اپراتور: نتیجه را تأیید کرد"], [/^Operator: reopened the result$/, "اپراتور: تأیید نتیجه را برداشت"],
    [/^Operator: froze the board$/, "اپراتور: جدول را ثابت کرد"], [/^Operator: unfroze the board$/, "اپراتور: جدول را آزاد کرد"],
    [/^Operator: took the board off air$/, "اپراتور: جدول را از آنتن برداشت"], [/^Operator: put the board back on air$/, "اپراتور: جدول را به آنتن برگرداند"],
    [/^(.+): finish not seen at the line, time estimated: check$/, "$1: عبور از خط دیده نشد، زمان تخمینی است؛ بررسی کنید"],
    [/^Finish rack: (.+)$/, "قفسهٔ پایان: $1"], [/^Tracker error: (.+)$/, "خطای ردیاب: $1"], [/^Tracking started on (\d+) cameras?$/, "ردیابی روی $1 دوربین شروع شد"], [/^(.+) started$/, "$1 شروع به کار کرد"],
    // counts inside summaries ("10 marbles · 29.7 m · 3 laps", map list lines)
    [/(\d+) marbles?\b/g, "$1 توپ"], [/ · (\d+) laps\b/g, " · $1 دور"], [/(\d+) cameras? \((\d+) set up\)/g, "$1 دوربین ($2 آماده)"],
    [/\blength not set\b/g, "طول تعیین نشده"], [/\bAddress not set\b/g, "نشانی تعیین نشده"], [/ · ([\d.]+) m$/, " · $1 متر"],
    [/: ([\d.]+ m) to ([\d.]+ m)$/, ": $1 تا $2"],
  ];
  /* ------------------------------------------------------------ sentences with markup inside, translated whole */
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const BLOCK = {
    "Click the centre of the track at the first mark this camera sees, then the next ones in race direction. Tip: rest a marble on each mark and click its centre.":
      "روی <b>اولین علامتی</b> که این دوربین می‌بیند، وسط مسیر کلیک کنید و بعد روی علامت‌های بعدی، در جهت حرکت مسابقه. نکته: یک توپ روی هر علامت بگذارید و روی مرکز آن کلیک کنید.",
    "This camera watches the finish rack. Click the centre of every slot, the first finisher's slot first. No distances needed. In step 5, click each marble sitting in the rack to learn its colours here.":
      "این دوربین <b>قفسهٔ پایان</b> را می‌بیند. روی مرکز همهٔ خانه‌ها کلیک کنید، از خانهٔ نفر اول شروع کنید. فاصله لازم نیست. در مرحلهٔ ۵ روی هر توپی که در قفسه نشسته کلیک کنید تا رنگش این‌جا یاد گرفته شود.",
    "Next click starts a new stretch: the first mark of that other part of the track. Then click its label and enter its distance.":
      "کلیک بعدی یک <b>تکهٔ جدید</b> شروع می‌کند: اولین علامتِ آن بخش دیگر مسیر. سپس روی برچسبش کلیک کنید و فاصله‌اش را وارد کنید.",
    "Remove every marble from the track and keep hands and people out of view. Capturing takes about a second.":
      "<b>همهٔ توپ‌ها را از مسیر بردارید</b> و دست و آدم‌ها را از جلوی دوربین‌ها دور نگه دارید. عکس‌برداری حدود یک ثانیه طول می‌کشد.",
    "Roll one marble alone through the course: every camera saves what it detects, labelled with that marble (real light, blur and shadows). A run with no marble at all collects what must be rejected.":
      "<b>فقط یک توپ</b> را در کل مسیر رها کنید: هر دوربین آنچه را تشخیص می‌دهد با نام همان توپ ذخیره می‌کند (با نور، تاری و سایهٔ واقعی). یک اجرا بدون هیچ توپی، چیزهایی را جمع می‌کند که باید رد شوند.",
    "Keeps the marbles, cameras and outputs; waypoints, empty track and colours are done again. To start from an existing route instead, use Copy as new route below.":
      "توپ‌ها، دوربین‌ها و خروجی‌ها می‌مانند؛ نقاط مسیر، مسیر خالی و رنگ‌ها دوباره انجام می‌شوند. برای شروع از روی یک مسیر موجود، از <b>کپی به‌عنوان مسیر جدید</b> در پایین استفاده کنید.",
    "Sent as Authorization: Bearer …": "به این شکل فرستاده می‌شود: <code>Authorization: Bearer …</code>",
    "To try MarbleTrack without hardware, start it with --demo.": "برای امتحان MarbleTrack بدون سخت‌افزار، آن را با <code>--demo</code> اجرا کنید.",
    "For checking the tracker on real races: every frame of every camera, the leaderboards that went out and all events are saved, and python -m marbletrack_app.replay <folder> runs the tracker again on them. About 0.5–1 GB per camera per minute.":
      "برای سنجیدن ردیاب روی مسابقه‌های واقعی: همهٔ فریم‌های همهٔ دوربین‌ها، جدول‌هایی که پخش شد و همهٔ رویدادها ذخیره می‌شوند و <code>python -m marbletrack_app.replay &lt;folder&gt;</code> ردیاب را دوباره روی آن‌ها اجرا می‌کند. حدود ۰٫۵ تا ۱ گیگابایت برای هر دوربین در هر دقیقه.",
  };
  const BLOCKR = [
    [/^The far end of this view has only one pixel per ([\d.]+) mm of track\. Positions there are less precise: move the camera closer or give the stretch to another camera\.$/,
      (m) => `در انتهای دور این نما، هر پیکسل ${esc(m[1])} میلی‌متر از مسیر است و مکان‌ها دقت کمتری دارند: دوربین را نزدیک‌تر کنید یا این تکه را به دوربین دیگری بسپارید.`],
    [/^Colours this camera could confuse: (.+)\.\s*Click those marbles once more in another spot\. If it stays, change the lighting or swap one marble for a clearly different colour\.$/,
      (m) => `<b>رنگ‌هایی که این دوربین ممکن است اشتباه بگیرد:</b> ${esc(m[1])}. روی این توپ‌ها یک بار دیگر در جای دیگری کلیک کنید. اگر هشدار ماند، نور را تغییر دهید یا یکی از توپ‌ها را با رنگی کاملاً متفاوت عوض کنید.`],
  ];
  const UNITS = { m: "متر", mm: "میلی‌متر", laps: "دور", ms: "میلی‌ثانیه", "/s": "در ثانیه", rows: "ردیف" };

  // numbers with units and ranges ("9.61–9.8 m", "10–31 fps", "11:14:28 PM") keep left-to-right order inside Persian text
  const LRI = "\u2066", PDI = "\u2069";
  const isolate = (t) => t.replace(/[+\-]?\d[\d.,:]*(?:\s*[–-]\s*\d[\d.,:]*)?(?:\s?(?:m|mm|ms|s|fps|px|PM|AM|×\d+))?(?![\w\u2069])/g, (x) => (/[–:]|\s/.test(x) ? LRI + x + PDI : x));
  function core(c) {
    let out = D[c];
    if (out == null) {
      const more = / \(\+(\d+) more\)$/.exec(c);
      if (more) { const head = core(c.slice(0, more.index)); return (head ?? c.slice(0, more.index)) + ` (و ${more[1]} مورد دیگر)`; }
      let x = c;
      for (const [re, rep] of R) x = x.replace(re, rep);
      if (x === c) return null;
      out = x;
    }
    return out;
  }
  function tr(s) {
    if (s.includes("\n")) return s.split("\n").map(tr).join("\n");
    const m = /^(\s*)([\s\S]*?)(\s*)$/.exec(s);
    const c = m[2];
    if (!c || !/[A-Za-z]/.test(c) || c.includes(LRI)) return s;
    const out = core(c);
    if (out == null) return s;
    return m[1] + isolate(out) + m[3];
  }
  window.mtT = tr;

  const SKIP = new Set(["SCRIPT", "STYLE", "TEXTAREA", "INPUT", "CODE", "PRE", "SVG", "CANVAS"]);
  const BLOCK_SEL = ".note > div, small, .hint, .head p, li, .empty-state p, p";
  function blocks(root) {
    const els = root.matches?.(BLOCK_SEL) ? [root] : [];
    if (root.querySelectorAll) els.push(...root.querySelectorAll(BLOCK_SEL));
    for (const el of els) {
      if (!el.firstElementChild || el.closest("[data-noi18n]")) continue;  // plain text: the text-node pass does it
      const text = el.textContent.replace(/\s+/g, " ").trim();
      let html = BLOCK[text];
      if (html == null) for (const [re, f] of BLOCKR) { const m = re.exec(text); if (m) { html = f(m); break; } }
      if (html != null) el.innerHTML = html;
    }
  }
  const ATTRS = ["title", "placeholder", "aria-label"];
  function attrs(el) {
    for (const a of ATTRS) { const v = el.getAttribute(a); if (v) { const t = tr(v); if (t !== v) el.setAttribute(a, t); } }
    const u = el.getAttribute("data-unit");
    if (u && UNITS[u]) { el.setAttribute("data-unit", UNITS[u]); el.style.setProperty("--u", UNITS[u].length); }
  }
  function walk(root) {
    if (root.nodeType === 3) { fixText(root); return; }
    if (root.nodeType !== 1 || SKIP.has(root.nodeName.toUpperCase()) || root.closest("[data-noi18n]")) return;
    blocks(root);
    attrs(root);
    root.querySelectorAll("[title],[placeholder],[aria-label],[data-unit]").forEach(attrs);
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.parentElement && (SKIP.has(n.parentElement.nodeName.toUpperCase()) || n.parentElement.closest("svg,[data-noi18n]")) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    const nodes = []; while (w.nextNode()) nodes.push(w.currentNode);
    nodes.forEach(fixText);
  }
  function fixText(n) {
    const p = n.parentElement;
    if (p && (SKIP.has(p.nodeName.toUpperCase()) || p.closest("svg,[data-noi18n]"))) return;
    const t = tr(n.nodeValue);
    if (t !== n.nodeValue) n.nodeValue = t;
  }
  const nativeConfirm = window.confirm.bind(window), nativePrompt = window.prompt.bind(window);
  window.confirm = (msg) => nativeConfirm(tr(String(msg)));
  window.prompt = (msg, def) => nativePrompt(tr(String(msg)), def);

  document.addEventListener("DOMContentLoaded", () => {
    addSwitch();
    walk(document.body);
    new MutationObserver((list) => {
      for (const m of list) {
        if (m.type === "characterData") fixText(m.target);
        else if (m.type === "attributes") attrs(m.target);
        else m.addedNodes.forEach(walk);
      }
    }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: [...ATTRS, "data-unit"] });
  });
})();
