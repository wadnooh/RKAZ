I18N = {
    "ar": {
        "app_title": "نظام متابعة الأعمال العام — مكتب خدمات خريص",
        "login": "دخول",
        "login_subtitle": "تسجيل الدخول",
        "username": "المستخدم",
        "password": "كلمة المرور",
        "remember_me": "تذكرني",
        "forgot_password": "نسيت كلمة المرور؟",
        "logout": "خروج",
        "search": "بحث",
        "footer": "شركة ركاز الإنجاز للمقاولات — مكتب خدمات خريص — جميع الحقوق محفوظة {year}",
        "bad_login": "اسم المستخدم أو كلمة المرور غير صحيحة",
        "inactive_user": "الحساب موقوف",
        "forgot_hint": "لإعادة تعيين كلمة المرور تواصل مع الدعم: 0596266407",
    },
    "en": {
        "app_title": "General Works Tracking — Khurais Services Office",
        "login": "Login",
        "login_subtitle": "Sign in",
        "username": "Username",
        "password": "Password",
        "remember_me": "Remember me",
        "forgot_password": "Forgot password?",
        "logout": "Logout",
        "search": "Search",
        "footer": "شركة ركاز الإنجاز للمقاولات — Khurais Services Office — All rights reserved {year}",
        "bad_login": "Invalid username or password",
        "inactive_user": "Account is inactive",
        "forgot_hint": "To reset your password contact support: 0596266407",
    },
}


def tr(lang, key, **kwargs):
    lang = lang if lang in I18N else "ar"
    text = I18N.get(lang, I18N["ar"]).get(key) or I18N["ar"].get(key) or key
    try:
        return text.format(**kwargs)
    except Exception:
        return text
