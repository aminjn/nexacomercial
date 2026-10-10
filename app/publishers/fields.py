"""Credential fields per account kind + step-by-step guides (Persian) — drives the dashboard form.

KIND_FIELDS: kind -> [(name, required, label, help)]   — `help` says exactly where the value comes from.
KIND_GUIDES: kind -> {"summary", "steps", "links", "notes"} — shown above the fields when the kind is chosen.

Regular kinds use the platform's official API with your own token. "<platform>_web" kinds instead
publish as a logged-in user through a headless browser: you log in yourself once from the dashboard
(live view of the browser), no password is stored and no captcha is solved automatically.
"""
from __future__ import annotations

from typing import Any

F = tuple[str, bool, str, str]

KIND_FIELDS: dict[str, list[F]] = {
    # ------------------------------------------------------------------ article / backlink destinations
    "telegraph": [
        ("author_name", False, "نام نویسنده", "اختیاری. اسمی که بالای مقاله نوشته می‌شود؛ مثلاً نام برندت."),
        ("author_url", False, "لینک نویسنده", "اختیاری. روی نام نویسنده کلیک‌پذیر می‌شود؛ مثلاً آدرس سایتت."),
        ("access_token", False, "توکن", "خالی بگذار؛ اولین انتشار خودش یک حساب تلگراف می‌سازد."),
    ],
    "wordpress": [
        ("base_url", False, "آدرس وبلاگ وردپرسی", "برای وردپرس روی هاست خودت: آدرس کامل وبلاگ، مثل https://blog.example.com"),
        ("username", False, "نام کاربری وردپرس", "همان نام کاربری که با آن وارد wp-admin می‌شوی (نه ایمیل)."),
        ("password", False, "رمز ورود وردپرس",
         "ساده‌ترین راه برای وردپرس روی هاست خودت: همان رمزی که با آن وارد wp-admin می‌شوی (از xmlrpc.php استفاده می‌شود). "
         "اگر Application Password بگذاری، آن ترجیح داده می‌شود."),
        ("app_password", False, "Application Password",
         "wp-admin ← کاربران ← نمایه‌ی شما ← پایین صفحه «Application Passwords» ← یک نام بنویس ← "
         "«Add New Application Password» ← رمز ۲۴ حرفی را کپی کن (فاصله‌ها مشکلی ندارند). رمز اصلی وردپرس را نگذار."),
        ("site", False, "آدرس سایت wordpress.com", "فقط اگر وبلاگت روی wordpress.com است: مثل myblog.wordpress.com"),
        ("token", False, "توکن wordpress.com", "فقط برای wordpress.com: از developer.wordpress.com/apps یک اپ بساز و توکن OAuth بگیر."),
        ("category_id", False, "شناسه‌ی دسته", "اختیاری. نوشته‌ها ← دسته‌ها ← موس را روی دسته ببر؛ عدد tag_ID در آدرس پایین مرورگر."),
        ("status", False, "وضعیت انتشار", "publish = منتشر شود، draft = پیش‌نویس بماند. خالی = publish"),
    ],
    "blogger": [
        ("blog_id", True, "شناسه‌ی وبلاگ", "در داشبورد Blogger آدرس مرورگر شبیه blogger.com/blog/posts/1234567890 است؛ آن عدد طولانی را کپی کن."),
        ("client_id", True, "Client ID گوگل", "از Google Cloud Console ← APIs & Services ← Credentials (مراحل بالا). شبیه ...apps.googleusercontent.com"),
        ("client_secret", True, "Client Secret گوگل", "همان صفحه‌ی Client ID، کنارش نوشته شده (شروع با GOCSPX-)."),
        ("refresh_token", True, "Refresh Token", "از OAuth Playground گوگل (مرحله‌ی ۶ بالا). شبیه 1//0g..."),
    ],
    "devto": [
        ("api_key", True, "API Key", "dev.to ← Settings ← Extensions ← پایین صفحه «DEV Community API Keys» ← یک توضیح بنویس ← Generate API Key."),
    ],
    "hashnode": [
        ("token", True, "Personal Access Token", "hashnode.com/settings/developer ← «Generate new token» ← کپی کن."),
        ("publication_id", True, "شناسه‌ی وبلاگ", "وارد داشبورد وبلاگت شو؛ آدرس شبیه hashnode.com/6543abc.../dashboard است. آن رشته‌ی وسط آدرس همین شناسه است."),
    ],
    "tumblr": [
        ("blog", True, "آدرس وبلاگ", "مثل myblog.tumblr.com"),
        ("consumer_key", True, "Consumer Key", "tumblr.com/oauth/apps ← Register application ← بعد از ساخت، «OAuth Consumer Key»."),
        ("consumer_secret", True, "Consumer Secret", "همان صفحه، «Secret Key» (روی show secret بزن)."),
        ("token", True, "Token", "api.tumblr.com/console ← Consumer Key و Secret را وارد کن ← Authenticate ← اجازه بده ← مقدار Token را کپی کن."),
        ("token_secret", True, "Token Secret", "همان صفحه‌ی console، زیر Token."),
    ],
    "medium": [
        ("token", True, "Integration Token", "medium.com ← Settings ← Security and apps ← Integration tokens. (مدیوم دیگر به حساب‌های جدید توکن نمی‌دهد؛ فقط اگر قبلاً داشتی.)"),
        ("user_id", False, "شناسه‌ی کاربر", "خالی بگذار؛ خودکار پیدا می‌شود."),
    ],
    "ghost": [
        ("url", True, "آدرس سایت Ghost", "مثل https://myghost.example.com — همان «API URL» در صفحه‌ی Integration."),
        ("admin_api_key", True, "Admin API Key", "Ghost Admin ← Settings ← Integrations ← Add custom integration ← «Admin API key» (شکل id:secret)."),
    ],
    "writeas": [
        ("collection", True, "نام وبلاگ (alias)", "اگر آدرس وبلاگت write.as/myblog است، بنویس myblog"),
        ("username", False, "نام کاربری Write.as", "خالی = همان نام وبلاگ."),
        ("password", False, "رمز Write.as", "ساده‌ترین راه: رمز ورود خودت؛ هر بار خودش وارد می‌شود."),
        ("token", False, "Access Token", "به‌جای رمز: با دستور مرحله‌ی ۲ بالا گرفته می‌شود (مقدار access_token در جواب)."),
    ],
    # ------------------------------------------------------------------ social networks
    "telegram": [
        ("bot_token", True, "توکن ربات", "در تلگرام به @BotFather پیام بده ← /newbot ← اسم و یوزرنیم ربات ← توکنی شبیه 123456789:AA... می‌دهد."),
        ("chat_id", True, "کانال یا گروه", "برای کانال عمومی: @یوزرنیم_کانال. برای کانال خصوصی: عدد منفی که با -100 شروع می‌شود (مرحله‌ی ۴ بالا)."),
    ],
    "x": [
        ("consumer_key", True, "API Key", "developer.x.com ← Developer Portal ← اپ تو ← Keys and tokens ← «API Key and Secret» ← Regenerate."),
        ("consumer_secret", True, "API Key Secret", "همان جا، کنار API Key."),
        ("access_token", True, "Access Token", "همان صفحه ← «Access Token and Secret» ← Generate. حتماً بعد از Read and write کردن دسترسی (مرحله‌ی ۳)."),
        ("access_token_secret", True, "Access Token Secret", "همان جا، کنار Access Token."),
    ],
    "linkedin": [
        ("access_token", True, "Access Token", "از ابزار OAuth Token Generator لینکدین (مرحله‌ی ۳ بالا). هر ۶۰ روز منقضی می‌شود."),
        ("author_urn", True, "شناسه‌ی نویسنده (URN)",
         "پروفایل شخصی: urn:li:person:XXXX (XXXX مقدار sub در مرحله‌ی ۴). صفحه‌ی شرکت: urn:li:organization:12345 "
         "(عدد در آدرس صفحه‌ی مدیریت شرکت linkedin.com/company/12345/admin)."),
    ],
    "facebook": [
        ("page_id", True, "شناسه‌ی صفحه (Page ID)", "صفحه‌ی فیسبوک ← About ← Page transparency؛ یا در Graph API Explorer جواب me/accounts مقدار id."),
        ("page_access_token", True, "توکن صفحه", "Graph API Explorer ← me/accounts ← مقدار access_token کنار صفحه‌ات (مراحل ۳ تا ۵ بالا)."),
    ],
    "instagram": [
        ("ig_user_id", True, "شناسه‌ی اکانت اینستاگرام", "Graph API Explorer ← {page-id}?fields=instagram_business_account ← مقدار id داخل instagram_business_account."),
        ("access_token", True, "توکن", "همان توکن صفحه‌ی فیسبوک (page token) که مجوزهای instagram_basic و instagram_content_publish دارد."),
        ("image_url", False, "تصویر", "خالی بگذار؛ تصویری که برای سایت انتخاب کرده‌ای استفاده می‌شود."),
    ],
    "threads": [
        ("user_id", True, "شناسه‌ی کاربر Threads", "در مرورگر باز کن: graph.threads.net/v1.0/me?access_token=توکن ← مقدار id."),
        ("access_token", True, "توکن", "developers.facebook.com ← اپ تو ← Use cases ← Threads API ← Settings ← User Token Generator ← Generate."),
    ],
    "mastodon": [
        ("instance", False, "آدرس سرور ماستودون", "سروری که اکانتت روی آن است، مثل https://mastodon.social"),
        ("access_token", True, "Access Token", "ماستودون ← Preferences ← Development ← New application ← اسم ← فقط write:statuses ← Submit ← روی اپ کلیک ← «Your access token»."),
    ],
    "bluesky": [
        ("handle", True, "هندل", "نام کاربری کامل بدون @، مثل myname.bsky.social"),
        ("app_password", True, "رمز (App Password یا رمز اصلی)", "پیشنهادی: Bluesky ← Settings ← Privacy and security ← App passwords ← Add App Password ← رمزی شبیه xxxx-xxxx-xxxx-xxxx. رمز اصلی اکانت هم کار می‌کند ولی امن‌تر نیست."),
    ],
    "reddit": [
        ("client_id", True, "Client ID", "reddit.com/prefs/apps ← create another app ← نوع script ← بعد از ساخت، رشته‌ی زیر «personal use script»."),
        ("client_secret", True, "Client Secret", "همان صفحه، مقدار جلوی «secret»."),
        ("username", True, "نام کاربری ردیت", "بدون u/"),
        ("password", True, "رمز ردیت", "رمز همان اکانت. ورود دومرحله‌ای (2FA) این اکانت باید خاموش باشد."),
        ("subreddit", True, "ساب‌ردیت", "بدون r/؛ ساب‌ردیت خودت یا جایی که قوانینش لینک گذاشتن را مجاز می‌داند."),
        ("post_type", False, "نوع پست", "link = پست لینک (پیش‌فرض)، self = پست متنی"),
    ],
    "pinterest": [
        ("access_token", True, "Access Token", "developers.pinterest.com ← My apps ← اپ تو ← Generate token (با دسترسی boards:read و pins:write)."),
        ("board_id", True, "شناسه‌ی برد", "با همان توکن: api.pinterest.com/v5/boards (مرحله‌ی ۴ بالا) ← مقدار id بردی که می‌خواهی."),
        ("image_url", False, "تصویر", "خالی بگذار؛ تصویر سایت استفاده می‌شود."),
    ],
    "webhook": [
        ("url", True, "آدرس Webhook", "آدرسی که n8n / Make / Zapier به تو می‌دهد (در n8n: نود Webhook ← Production URL)."),
        ("secret", False, "رمز", "اختیاری. در هدر X-Webhook-Secret فرستاده می‌شود تا طرف مقابل درخواست را تأیید کند."),
    ],
}

KIND_GUIDES: dict[str, dict[str, Any]] = {
    "telegraph": {
        "summary": "ساده‌ترین مقصد؛ هیچ حسابی لازم نیست. همه‌ی کادرها را می‌توانی خالی بگذاری.",
        "steps": ["فقط نام نویسنده و لینک سایتت را (اختیاری) بنویس و ذخیره کن."],
        "links": [("telegra.ph", "https://telegra.ph")],
    },
    "wordpress": {
        "summary": "برای وبلاگ وردپرسی روی هاست خودت (یا wordpress.com).",
        "steps": [
            "وارد wp-admin وبلاگ شو.",
            "منوی «کاربران» ← «نمایه‌ی شما» (Users → Profile).",
            "پایین صفحه بخش «Application Passwords»: یک نام مثل nexa بنویس و «Add New Application Password» را بزن.",
            "رمزی که نشان می‌دهد را کپی کن (فقط یک بار نشان داده می‌شود).",
            "در فرم: آدرس وبلاگ، نام کاربری و همین رمز را بگذار.",
        ],
        "links": [("راهنمای Application Passwords", "https://make.wordpress.org/core/2020/11/05/application-passwords-integration-guide/")],
        "notes": "سایت باید HTTPS داشته باشد. اگر بخش Application Passwords را نمی‌بینی، افزونه‌ی امنیتی آن را بسته است.",
    },
    "blogger": {
        "summary": "بلاگر به OAuth گوگل نیاز دارد؛ یک بار انجامش می‌دهی (حدود ۱۰ دقیقه).",
        "steps": [
            "به console.cloud.google.com برو و یک پروژه‌ی جدید بساز.",
            "APIs & Services ← Library ← «Blogger API v3» را پیدا کن و Enable بزن.",
            "APIs & Services ← OAuth consent screen ← نوع External ← اسم اپ و ایمیلت ← ذخیره. بعد «Publish app» را بزن تا توکن بعد از ۷ روز منقضی نشود.",
            "Credentials ← Create credentials ← OAuth client ID ← نوع Web application.",
            "در Authorized redirect URIs این را اضافه کن: https://developers.google.com/oauthplayground ← Create. Client ID و Client Secret را کپی کن.",
            "به developers.google.com/oauthplayground برو ← چرخ‌دنده‌ی بالا راست ← «Use your own OAuth credentials» ← Client ID و Secret را بگذار.",
            "در کادر سمت چپ بنویس https://www.googleapis.com/auth/blogger ← Authorize APIs ← با همان حساب گوگلِ صاحب وبلاگ وارد شو.",
            "«Exchange authorization code for tokens» ← مقدار Refresh token را کپی کن.",
            "Blog ID را از آدرس داشبورد بلاگر بردار (عدد طولانی داخل آدرس).",
        ],
        "links": [("Google Cloud Console", "https://console.cloud.google.com/"),
                  ("OAuth Playground", "https://developers.google.com/oauthplayground")],
    },
    "devto": {
        "summary": "یک کلید API کافی است.",
        "steps": ["وارد dev.to شو ← Settings ← Extensions.", "پایین صفحه «DEV Community API Keys»: توضیح بنویس ← Generate API Key ← کپی."],
        "links": [("صفحه‌ی کلیدها", "https://dev.to/settings/extensions")],
    },
    "hashnode": {
        "summary": "توکن شخصی + شناسه‌ی وبلاگ.",
        "steps": [
            "hashnode.com/settings/developer ← Generate new token ← کپی.",
            "وارد داشبورد وبلاگت شو؛ رشته‌ی وسط آدرس (hashnode.com/<این>/dashboard) شناسه‌ی وبلاگ است.",
        ],
        "links": [("صفحه‌ی توکن", "https://hashnode.com/settings/developer")],
    },
    "tumblr": {
        "summary": "یک اپ می‌سازی و با کنسول تامبلر توکن می‌گیری.",
        "steps": [
            "tumblr.com/oauth/apps ← Register application. برای Website و Callback URL آدرس سایتت را بگذار.",
            "بعد از ساخت، OAuth Consumer Key و Secret Key را کپی کن.",
            "api.tumblr.com/console ← همان دو مقدار را وارد کن ← Authenticate ← Allow.",
            "در صفحه‌ی بعد Token و Token Secret نشان داده می‌شود؛ کپی کن.",
        ],
        "links": [("ساخت اپ", "https://www.tumblr.com/oauth/apps"), ("کنسول", "https://api.tumblr.com/console")],
    },
    "medium": {
        "summary": "مدیوم دیگر برای حساب‌های جدید توکن صادر نمی‌کند.",
        "steps": ["اگر قبلاً توکن داشتی: medium.com ← Settings ← Security and apps ← Integration tokens."],
        "links": [("تنظیمات مدیوم", "https://medium.com/me/settings/security")],
        "notes": "اگر توکن نداری، این مقصد را رها کن و از Dev.to / Hashnode / WordPress استفاده کن.",
    },
    "ghost": {
        "summary": "یک Integration در پنل Ghost بساز.",
        "steps": ["Ghost Admin ← Settings ← Integrations ← Add custom integration ← یک اسم.",
                  "Admin API key و API URL را کپی کن."],
        "links": [("مستندات", "https://ghost.org/integrations/custom-integrations/")],
    },
    "writeas": {
        "summary": "توکن را با یک دستور می‌گیری.",
        "steps": [
            "در ترمینال سرور (یا هر جا) این را بزن (یوزر و رمز Write.as خودت):",
            'curl -s https://write.as/api/auth/login -H "Content-Type: application/json" -d \'{"alias":"USER","pass":"PASS"}\'',
            "از جواب، مقدار access_token را کپی کن.",
        ],
        "links": [("مستندات API", "https://developers.write.as/docs/api/")],
    },
    "telegram": {
        "summary": "یک ربات می‌سازی و آن را ادمین کانالت می‌کنی.",
        "steps": [
            "در تلگرام @BotFather را باز کن ← /newbot ← یک اسم ← یک یوزرنیم که به bot ختم شود ← توکن را کپی کن.",
            "به کانالت برو ← Administrators ← Add Admin ← ربات را پیدا کن ← دسترسی Post Messages را بده.",
            "کانال عمومی: chat_id همان @یوزرنیم کانال است. تمام!",
            "کانال خصوصی: یک پیام در کانال بفرست، بعد در مرورگر api.telegram.org/bot<توکن>/getUpdates را باز کن و عدد جلوی \"chat\":{\"id\": را (با -100 شروعش) کپی کن.",
        ],
        "links": [("BotFather", "https://t.me/BotFather")],
    },
    "x": {
        "summary": "به حساب توسعه‌دهنده‌ی X نیاز داری (پلن رایگان سقف ماهانه‌ی محدودی دارد).",
        "steps": [
            "developer.x.com ← Sign up for Free Account ← فرم استفاده را پر کن.",
            "در Developer Portal اپ پیش‌فرض را باز کن.",
            "User authentication settings ← Set up ← App permissions: «Read and write» ← نوع: Web App ← Callback و Website را آدرس سایتت بگذار ← Save.",
            "Keys and tokens ← API Key and Secret ← Regenerate ← هر دو را کپی کن.",
            "همان صفحه ← Access Token and Secret ← Generate ← هر دو را کپی کن (اگر قبل از مرحله‌ی ۳ ساخته بودی، دوباره Regenerate کن).",
        ],
        "links": [("Developer Portal", "https://developer.x.com/en/portal/dashboard")],
    },
    "linkedin": {
        "summary": "یک اپ لینکدین می‌سازی و توکن ۶۰ روزه می‌گیری.",
        "steps": [
            "linkedin.com/developers/apps ← Create app ← یک صفحه‌ی شرکت (LinkedIn Page) باید به اپ وصل کنی.",
            "تب Products: «Share on LinkedIn» و «Sign In with LinkedIn using OpenID Connect» را Request access کن.",
            "linkedin.com/developers/tools/oauth/token-generator ← اپ را انتخاب کن ← تیک openid، profile و w_member_social ← Request access token ← کپی.",
            "در مرورگر/ترمینال: curl -H \"Authorization: Bearer توکن\" https://api.linkedin.com/v2/userinfo ← مقدار sub را بردار و بنویس urn:li:person:<sub>",
        ],
        "links": [("اپ‌های لینکدین", "https://www.linkedin.com/developers/apps"),
                  ("Token Generator", "https://www.linkedin.com/developers/tools/oauth/token-generator")],
        "notes": "پست از طرف صفحه‌ی شرکت (urn:li:organization) به تأیید Community Management API لینکدین نیاز دارد. توکن هر ۶۰ روز باید عوض شود.",
    },
    "facebook": {
        "summary": "توکن صفحه را از Graph API Explorer می‌گیری.",
        "steps": [
            "developers.facebook.com ← My Apps ← Create App ← نوع Business (یا use case «Manage everything on your Page»).",
            "Tools ← Graph API Explorer ← اپ را انتخاب کن.",
            "Permissions: pages_show_list، pages_read_engagement، pages_manage_posts ← Generate Access Token ← اجازه بده.",
            "(برای توکن بلندمدت) در Access Token Debugger توکن را Extend کن و آن را در Explorer بگذار.",
            "در Explorer بنویس me/accounts ← Submit ← جلوی صفحه‌ات id = Page ID و access_token = توکن صفحه.",
        ],
        "links": [("Graph API Explorer", "https://developers.facebook.com/tools/explorer/"),
                  ("Access Token Debugger", "https://developers.facebook.com/tools/debug/accesstoken/")],
        "notes": "اگر توکن را از یک توکن کاربر بلندمدت (مرحله‌ی ۴) گرفته باشی، توکن صفحه منقضی نمی‌شود.",
    },
    "instagram": {
        "summary": "فقط اکانت Business/Creator که به یک صفحه‌ی فیسبوک وصل است.",
        "steps": [
            "در اپ اینستاگرام: Settings ← Account type ← Switch to professional account.",
            "اکانت را به صفحه‌ی فیسبوکت وصل کن (Meta Business Suite یا تنظیمات صفحه ← Linked accounts).",
            "مراحل فیسبوک را انجام بده، ولی در Permissions این‌ها را هم تیک بزن: instagram_basic، instagram_content_publish.",
            "در Graph API Explorer بنویس: <Page ID>?fields=instagram_business_account ← مقدار id داخل جواب = ig_user_id.",
            "access_token = توکن صفحه (از me/accounts).",
        ],
        "links": [("Graph API Explorer", "https://developers.facebook.com/tools/explorer/")],
        "notes": "پست اینستاگرام حتماً تصویر می‌خواهد؛ برای سایت در صفحه‌ی «سایت‌ها» تصویر انتخاب کن.",
    },
    "threads": {
        "summary": "از طریق اپ Meta با use case مخصوص Threads.",
        "steps": [
            "developers.facebook.com ← Create App ← use case «Access the Threads API».",
            "Use cases ← Threads API ← Customize: مجوزهای threads_basic و threads_content_publish.",
            "App roles ← Roles ← Add People ← Threads Tester ← اکانت Threads خودت. بعد در اپ Threads: Settings ← Account ← Website permissions ← Invites ← قبول کن.",
            "Use cases ← Threads API ← Settings ← User Token Generator ← Generate ← توکن را کپی کن.",
            "در مرورگر باز کن: https://graph.threads.net/v1.0/me?access_token=توکن ← مقدار id = user_id.",
        ],
        "links": [("Meta for Developers", "https://developers.facebook.com/apps/")],
    },
    "mastodon": {
        "summary": "در خود ماستودون یک اپ می‌سازی؛ دو دقیقه کار.",
        "steps": [
            "وارد سرور ماستودونت شو ← Preferences ← Development ← New application.",
            "یک اسم بنویس، در Scopes فقط write:statuses را تیک بزن ← Submit.",
            "روی اسم اپ کلیک کن ← «Your access token» را کپی کن.",
        ],
        "links": [("mastodon.social/settings/applications", "https://mastodon.social/settings/applications")],
    },
    "bluesky": {
        "summary": "یک App Password کافی است.",
        "steps": ["bsky.app ← Settings ← Privacy and security ← App passwords ← Add App Password ← یک اسم ← رمز را کپی کن.",
                  "هندل = نام کاربری کامل بدون @ (مثل name.bsky.social)."],
        "links": [("App passwords", "https://bsky.app/settings/app-passwords")],
    },
    "reddit": {
        "summary": "یک اپ نوع script در ردیت می‌سازی.",
        "steps": [
            "reddit.com/prefs/apps ← پایین صفحه «create another app».",
            "name: هر چیزی، نوع: script، redirect uri: http://localhost:8080 ← create app.",
            "رشته‌ی زیر «personal use script» = client_id؛ مقدار secret = client_secret.",
            "نام کاربری و رمز همان اکانت ردیت را بگذار (2FA باید خاموش باشد).",
        ],
        "links": [("اپ‌های ردیت", "https://www.reddit.com/prefs/apps")],
        "notes": "ردیت ممکن است برای اپ‌های جدید تأیید بخواهد. فقط در ساب‌ردیت‌هایی پست کن که لینک گذاشتن را مجاز می‌دانند، وگرنه اکانت بن می‌شود.",
    },
    "pinterest": {
        "summary": "یک اپ پینترست + شناسه‌ی برد.",
        "steps": [
            "اکانت پینترستت را Business کن.",
            "developers.pinterest.com ← My apps ← Connect app ← فرم را پر کن (دسترسی Trial کافی است برای شروع).",
            "در صفحه‌ی اپ ← Generate token با دسترسی boards:read، pins:read و pins:write ← کپی.",
            "در ترمینال: curl -H \"Authorization: Bearer توکن\" https://api.pinterest.com/v5/boards ← مقدار id برد مورد نظر.",
        ],
        "links": [("Pinterest Developers", "https://developers.pinterest.com/apps/")],
        "notes": "پینترست تصویر می‌خواهد؛ برای سایت تصویر انتخاب کن. در دسترسی Trial پین‌ها ممکن است فقط برای خودت دیده شوند.",
    },
    "webhook": {
        "summary": "برای فرستادن محتوا به n8n / Make / Zapier و از آن‌جا به هر سرویس دیگری.",
        "steps": ["در n8n یک ورک‌فلو با نود Webhook (POST) بساز و Production URL را کپی کن.",
                  "محتوا به شکل JSON (متن، لینک، هشتگ‌ها، تصویر) به آن آدرس فرستاده می‌شود."],
        "links": [],
    },
}

# ---------------------------------------------------------------- log in as a user (browser)

_HANDLE: F = ("handle", False, "نام کاربری", "اختیاری؛ فقط برای این‌که در لیست بدانی کدام اکانت است. رمز لازم نیست.")
WEB_FIELDS: dict[str, list[F]] = {
    "x_web": [_HANDLE],
    "linkedin_web": [_HANDLE],
    "facebook_web": [_HANDLE, ("page_url", False, "آدرس صفحه",
                               "خالی = روی پروفایل خودت پست می‌گذارد. برای صفحه‌ای که مدیرش هستی آدرس کاملش را بده، "
                               "مثل https://www.facebook.com/mypage (اول در فیسبوک به آن صفحه سوییچ کرده باش).")],
    "instagram_web": [_HANDLE, ("image_url", False, "تصویر", "خالی بگذار؛ تصویری که برای سایت انتخاب کرده‌ای استفاده می‌شود.")],
    "threads_web": [_HANDLE],
    "pinterest_web": [_HANDLE, ("board", True, "اسم برد", "اسم دقیق یکی از بردهایت در پینترست، همان‌طور که در پروفایلت نوشته شده.")],
    "reddit_web": [_HANDLE, ("subreddit", True, "ساب‌ردیت", "بدون r/؛ جایی که قوانینش لینک گذاشتن را مجاز می‌داند.")],
    "mastodon_web": [_HANDLE, ("instance", True, "آدرس سرور ماستودون", "مثل https://mastodon.social")],
    "telegram_web": [_HANDLE, ("channel", True, "کانال یا گروه", "یوزرنیم کانال، مثل @mychannel — خودت باید ادمینش باشی.")],
    "medium_web": [_HANDLE],
    "tumblr_web": [_HANDLE, ("blog", False, "اسم وبلاگ", "اختیاری؛ اگر چند وبلاگ داری، اسم همانی که باید در آن پست شود.")],
    "devto_web": [_HANDLE],
}
WEB_LOGIN_STEPS = [
    "همین فرم را ذخیره کن (رمز و توکن لازم نیست).",
    "در لیست اکانت‌ها روی دکمه‌ی «ورود با مرورگر» این اکانت بزن؛ یک مرورگر واقعی روی سرور (پشت v2ray) باز می‌شود و تصویرش را می‌بینی.",
    "مثل همیشه وارد شو: روی کادر کلیک کن، متن را در کادر زیر تصویر بنویس و «ارسال متن» را بزن. کد تأیید پیامکی یا ایمیلی را هم همین‌طور وارد کن.",
    "وقتی صفحه‌ی اصلی اکانتت را دیدی، «تمام شد، ورود ذخیره شود» را بزن. از این به بعد پست‌ها با همین ورود گذاشته می‌شوند.",
    "یک «تست» بزن. اگر بعداً سایت از اکانت خارجت کرد، همین مراحل را تکرار کن.",
]
WEB_NOTE = ("این روش رسمی نیست: سایت‌ها ورود و پست خودکار را دوست ندارند و ممکن است تأیید هویت بخواهند یا اکانت را "
            "موقتاً قفل کنند. اول با یک اکانت غیرمهم امتحان کن، فاصله‌ی بین پست‌ها را زیاد بگذار و زبان اکانت را "
            "انگلیسی کن تا دکمه‌ها پیدا شوند. اگر ظاهر سایت عوض شود، ممکن است این روش تا آپدیت بعدی کار نکند.")
KIND_FIELDS.update(WEB_FIELDS)
KIND_FIELDS["blogger_email"] = [
    ("blog_url", True, "آدرس وبلاگ", "همان آدرسی که وبلاگت با آن باز می‌شود، مثل melkjet.blogspot.com"),
    ("blog_email", True, "ایمیل انتشار بلاگر", "Blogger ← Settings ← Email ← Publish email address ← یک کلمه بنویس و "
                                                "«Publish emails immediately» را انتخاب کن ← آدرسی شبیه melkjet.xyz123@blogger.com ← همان را این‌جا بگذار."),
]
KIND_GUIDES["blogger_email"] = {
    "summary": "ساده‌ترین راه بلاگر: فقط یک آدرس ایمیل؛ بدون Google Cloud و توکن.",
    "steps": [
        "در blogger.com وارد وبلاگت شو ← Settings ← پایین صفحه بخش Email ← «Publish email address».",
        "یک کلمه‌ی دلخواه بنویس (مثلاً melkjet123) و «Publish emails immediately» را انتخاب کن.",
        "آدرسی که ساخته می‌شود (مثل you.melkjet123@blogger.com) را در کادر «ایمیل انتشار بلاگر» بگذار، آدرس وبلاگ را هم بنویس و ذخیره کن.",
        "یک بار برای کل برنامه: در «تنظیمات ← ایمیل فرستنده» یک ایمیل برای فرستادن پست‌ها بگذار و «ایمیل آزمایشی» را بزن.",
    ],
    "links": [("Blogger", "https://www.blogger.com/")],
    "notes": "این آدرس مثل رمز است؛ هر کسی داشته باشد می‌تواند در وبلاگت پست بگذارد. جایی منتشرش نکن.",
}
for _k in WEB_FIELDS:
    KIND_GUIDES[_k] = {"summary": "ورود با نام کاربری و رمز خودت، بدون API و توکن.", "steps": WEB_LOGIN_STEPS,
                       "links": [], "notes": WEB_NOTE}

SECRET_HINTS = ("token", "secret", "password", "key", "blog_email")


def is_secret(name: str) -> bool:
    return any(h in name for h in SECRET_HINTS)


def field_names(kind: str) -> list[str]:
    return [f[0] for f in KIND_FIELDS.get(kind, [])]


def missing_fields(kind: str, creds: dict[str, Any]) -> list[str]:
    return [f[0] for f in KIND_FIELDS.get(kind, []) if f[1] and not str(creds.get(f[0], "")).strip()]


def masked(creds: dict[str, Any]) -> dict[str, str]:
    out = {}
    for k, v in creds.items():
        v = str(v)
        out[k] = (v[:3] + "…" + v[-2:] if len(v) > 8 else "•••") if is_secret(k) and v else v
    return out
