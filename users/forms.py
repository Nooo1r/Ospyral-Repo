from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth import get_user_model, authenticate
from django.contrib.auth.backends import ModelBackend
from .models import TextProduct, Currency
import bleach
from django.forms import modelformset_factory
import re
from PIL import Image
from io import BytesIO
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.exceptions import ValidationError
from django.forms.widgets import HiddenInput
from django.core.validators import MinValueValidator, MaxValueValidator
from decimal import Decimal

from .models import Artwork, Lesson, Profile, ArtworkImage

CustomUser = get_user_model()

SEPARATOR = "\n\n---PAGEBREAK---\n\n"

NSFW_CATEGORY_VALUES = {
    # Опасные системы и закрытые знания (NSFW)
    "manipulation_systems", "social_engineering", "control_influence",
    "disruptive_thinking", "mass_control_structures", "cognitive_traps",
    # Эротические материалы (NSFW)
    "erotica_psychosexual", "provocative_literature", "power_domination_psych",
    "intimate_manifestos", "corporeality_borders",
}

# Разрешённый HTML (если ты оставляешь поддержку HTML-тегов в тексте)
BLEACH_ALLOWED_TAGS = set(bleach.sanitizer.ALLOWED_TAGS).union({"p", "br", "strong", "em", "ul", "li", "a"})
BLEACH_ALLOWED_ATTRS = {"a": ["href", "title", "rel", "target"]}


class CustomUserCreationForm(UserCreationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Применяем базовый CSS класс ко всем полям
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})
        # Если нужно задать дополнительные атрибуты, добавьте их здесь.
        # Например, можно добавить placeholder или другие data-атрибуты.

    class Meta:
        model = CustomUser
        fields = ['username', 'email', 'password1', 'password2']
        
    cf_turnstile_response = forms.CharField(
        widget=forms.HiddenInput(),
        required=True
    )

class EmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        # Если передали email через kwargs, используем его, иначе – значение username
        email = kwargs.get('email', username)
        try:
            user = CustomUser.objects.get(email=email.strip().lower())
        except CustomUser.DoesNotExist:
            return None
        if user.check_password(password):
            return user
        return None


class CustomAuthenticationForm(AuthenticationForm):
    email = forms.EmailField(label="Email", required=True)
    password = forms.CharField(label="Пароль", widget=forms.PasswordInput, required=True)

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        super().__init__(request, *args, **kwargs)
        # Удаляем поле username, если оно создаётся по умолчанию
        if 'username' in self.fields:
            del self.fields['username']
        # Применяем базовый CSS класс для полей формы
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})
        self.fields['email'].widget.attrs['placeholder'] = 'Введите email'
        self.fields['password'].widget.attrs['placeholder'] = 'Введите пароль'


    def clean(self):
        # Получаем введённые данные
        email = self.cleaned_data.get('email')
        password = self.cleaned_data.get('password')

        if email and password:
            # Можно нормализовать email, если требуется (например, привести к нижнему регистру)
            email = email.strip().lower()
            # Выполняем аутентификацию по email и паролю
            self.user_cache = authenticate(self.request, username=email, password=password)
            if self.user_cache is None:
                self.add_error(None, "Неверный email или пароль.")
                return self.cleaned_data
            else:
                self.confirm_login_allowed(self.user_cache)
        return self.cleaned_data

    def get_user(self):
        return self.user_cache



class ProfileEditForm(forms.ModelForm):
    # Дополнительное поле для редактирования цвета фона профиля
    background_color = forms.CharField(
        label="Цвет фона",
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={'type': 'color'})
    )

    class Meta:
        model = CustomUser
        fields = ['username', 'email', 'profile_image']

    def __init__(self, *args, **kwargs):
        super(ProfileEditForm, self).__init__(*args, **kwargs)
        # Инициализация поля background_color из связанного профиля
        if self.instance and hasattr(self.instance, 'profile'):
            self.fields['background_color'].initial = self.instance.profile.background_color
        else:
            self.fields['background_color'].initial = '#3498db'

    def save(self, commit=True):
        # Сначала сохраняем объект пользователя
        user = super(ProfileEditForm, self).save(commit=False)
        # Получаем значение цвета из данных формы
        background_color = self.cleaned_data.get('background_color', '#3498db')

        if commit:
            user.save()
        
        # Получаем или создаем профиль, связанный с пользователем
        profile, _ = Profile.objects.get_or_create(user=user)
        profile.background_color = background_color
        if commit:
            profile.save()
        
        return user




class TextProductForm(forms.ModelForm):
 
    # Новое поле title с ограничением 5–60
    title = forms.CharField(
        min_length=5, max_length=60,
        widget=forms.TextInput(attrs={
            'placeholder': 'Название продукта',
            'minlength': '5', 'maxlength': '60'
        }),
        label="Название"
    )
    # Новое поле description с ограничением 10–300
    description = forms.CharField(
        min_length=10, max_length=300,
        widget=forms.Textarea(attrs={
            'rows': 3,
            'placeholder': 'Краткое описание (10–300 симв.)',
            'minlength': '10', 'maxlength': '300'
        }),
        label="Краткое описание"
    )
    content = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 15, 'cols': 80}),
        label="Контент продукта"
    )                            
    currency = forms.ChoiceField(
        choices=TextProduct._meta.get_field('currency').choices,
        widget=forms.RadioSelect(attrs={'class': ''}),
        initial=TextProduct._meta.get_field('currency').default
    )

    price = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[
            MinValueValidator(0),
            MaxValueValidator(Decimal('10000000.00'))
        ],
        widget=forms.NumberInput(attrs={
            'min': '0',
            'max': '10000000',
            'step': '0.01',
            'class': 'form-control',  
        }),
        help_text="От 0 до 10 000 000.00, не более двух знаков после точки"
    )

    pages = forms.IntegerField(
        min_value=1, max_value=100,
        label="Страницы"
    )

    keywords = forms.CharField(
        required=False,
        label="Теги",
        help_text="Введите теги через #, не больше 10"
    )

    class Meta:
        model = TextProduct
        fields = ['title', 'description', 'category', 'keywords', 'currency', 'price', 'pages', 'content']

    def clean_content(self):
        content = self.cleaned_data.get('content', '')

        # 1) Санитизация HTML
        clean_content = bleach.clean(
            content,
            tags=BLEACH_ALLOWED_TAGS,
            attributes=BLEACH_ALLOWED_ATTRS,
            strip=True,
        )

        # 2) Безопасная linkify с кастомным callback (вместо bleach.linkifier.NOFOLLOW)
        from bleach.linkifier import Linker

        def add_target_rel(attrs, new):
            # attrs — dict c ключами вида (namespace, attr)
            # добавим target="_blank"
            attrs[(None, "target")] = "_blank"
            # аккуратно нарастим rel
            existing_rel = attrs.get((None, "rel"), "") or ""
            tokens = set(filter(None, existing_rel.split()))
            tokens.update({"nofollow", "noopener", "noreferrer"})
            attrs[(None, "rel")] = " ".join(sorted(tokens))
            return attrs

        linker = Linker(callbacks=[add_target_rel])  # без НЕсуществующего NOFOLLOW
        clean_content = linker.linkify(clean_content)

        # (необязательно) предупреждение о длине
        if len(clean_content) > 200_000:
            self._warn_long_content = True

        return clean_content

    def clean(self):
        cleaned = super().clean()
        currency = cleaned.get('currency')
        price    = cleaned.get('price')
        pages    = cleaned.get('pages')
        content  = cleaned.get('content') or ''

        # Валидация минимальной цены для USDT
        if currency == Currency.USDT and price is not None and price < 5:
            self.add_error('price', 'Минимальная цена в USDT — 5.')

        # Проверка на число страниц × 2500 символов
        if pages and len(content) > pages * 2500:
            self.add_error(
                'content',
                f'Текст слишком длинный для {pages} страниц: максимум 2500 символов на страницу.'
            )

        # Проверка тегов через #
        tags_str = cleaned.get('keywords', '')
        tags = re.findall(r'#(\w+)', tags_str)
        # Проверяем количество тегов
        if len(tags) > 10:
            self.add_error('keywords', 'Не более 10 тегов.')

        # Проверяем длину каждого тега
        for tag in tags:
            if len(tag) < 3 or len(tag) > 30:
                self.add_error(
                    'keywords',
                    f'Тег «{tag}» должен быть от 3 до 30 символов.'
                )
        # Сохраняем теги как CSV
        cleaned['keywords'] = ','.join(tags)

        actual_pages = len((content or '').split(SEPARATOR))
        if not (1 <= actual_pages <= 100):
            self.add_error('pages', 'Количество страниц должно быть от 1 до 100.')
        # Если скрытое поле pages не совпало — подправим на фактическое
        if pages != actual_pages:
            cleaned['pages'] = actual_pages

        # --- Автоматический NSFW по категории ---
        category = cleaned.get('category')
        self.instance.nsfw = bool(category in NSFW_CATEGORY_VALUES)

        return cleaned



    def save(self, commit=True, owner=None):
        # Получаем экземпляр продукта, не сохраняя ещё в базу
        instance = super(TextProductForm, self).save(commit=False)
        # Получаем очищенный контент
        content = self.cleaned_data['content']
        # Шифруем контент с использованием метода модели
        instance.encrypted_content = instance.encrypt_content(content)
        if owner:
            instance.owner = owner
        if commit:
            instance.save()
        return instance

class ArtworkForm(forms.ModelForm):
    title = forms.CharField(
        min_length=1, max_length=28,
        widget=forms.TextInput(attrs={'maxlength': '28'}),
        label="Название"
    )
    description = forms.CharField(
        min_length=10, max_length=300,
        required=False,
        widget=forms.Textarea(attrs={'rows': 3, 'maxlength': '300'}),
        label="Описание"
    )

    available_copies = forms.IntegerField(
        min_value=1,
        max_value=4000,
        initial=1,
        label="Количество копий",
        help_text="Количество копий для продажи"
    )

    keywords = forms.CharField(
        required=False,
        label="Теги",
        help_text="Введите до 10 тегов через #"
    )
    price = forms.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(Decimal('10000000.00'))],
        widget=forms.NumberInput(attrs={'min': '0', 'max': '10000000', 'step': '0.01'}),
        label="Цена"
    )

    currency = forms.ChoiceField(
        choices=Artwork._meta.get_field('currency').choices,
        initial=Artwork._meta.get_field('currency').default,
        widget=forms.RadioSelect,
        label="Валюта"
    )

    nsfw = forms.BooleanField(
        required=False,
        label="NSFW",
        widget=forms.CheckboxInput(attrs={
            'style': 'margin-right: 8px; transform: scale(1.2); vertical-align: middle;',
        })
    )

    class Meta:
        model = Artwork
        fields = ['title', 'available_copies', 'description', 'keywords', 'category', 'currency', 'price', 'original_image', 'nsfw']

    def clean_keywords(self):
        tags_str = self.cleaned_data.get('keywords', '')
        tags = re.findall(r'#(\w+)', tags_str)
        if len(tags) > 10:
            raise forms.ValidationError('Не более 10 тегов.')
        for t in tags:
            if len(t) < 3 or len(t) > 30:
                raise forms.ValidationError(f'Тег "{t}" должен быть от 3 до 30 символов.')
        return ','.join(tags)

    def clean(self):
        cleaned = super().clean()
        nsfw = cleaned.get('nsfw')
        # Получаем данные об областях цензуры из скрытого поля или из существующей модели
        cens_areas = self.data.get('censored_areas') or getattr(self.instance, 'censored_areas', None)
        currency = cleaned.get('currency')
        price    = cleaned.get('price')        
        
        # Проверяем только для уже созданных артворков (редактирование/pages этап)
        if nsfw and not cens_areas and self.instance.pk:
            raise forms.ValidationError(
                'Для NSFW-артворков необходимо добавить области цензуры.'
            )
        
        if currency == Currency.USDT and price is not None:
            MIN_USDT = Decimal('5.00')
            if price < MIN_USDT:
                self.add_error(
                    'price',
                    f'Минимальная цена при оплате в USDT — {MIN_USDT} USDT.'
                )
        return cleaned

        
class ArtworkImageForm(forms.ModelForm):
    class Meta:
        model = ArtworkImage
        fields = ['image', 'order']
        widgets = {'order': HiddenInput()}

    def clean_image(self):
        image = self.cleaned_data.get('image')
        if image:
            max_size = 5 * 1024 * 1024
            if image.size > max_size:
                raise ValidationError('Максимальный размер файла — 5 МБ.')
            valid_mime = ['image/jpeg', 'image/png']
            if image.content_type not in valid_mime:
                raise ValidationError('Допустим только JPEG или PNG.')
        return image


    def save(self, commit=True):
        instance = super().save(commit=False)
        image = self.cleaned_data.get('image')
        if image:
            # Открываем через Pillow
            img = Image.open(image)
            # Если ширина больше 1200px — ресайзим с сохранением пропорций
            max_width = 1200
            if img.width > max_width:
                ratio = max_width / float(img.width)
                new_height = int(img.height * ratio)
                img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)

            # Сохраняем в буфер с качеством 80%
            buffer = BytesIO()
            fmt = 'JPEG' if image.content_type == 'image/jpeg' else 'PNG'
            img.save(buffer, format=fmt, quality=80, optimize=True)
            buffer.seek(0)

            # Создаём новый UploadedFile
            new_image = InMemoryUploadedFile(
                buffer,
                field_name='image',
                name=image.name,
                content_type=image.content_type,
                size=buffer.getbuffer().nbytes,
                charset=None
            )
            instance.image = new_image

        if commit:
            instance.save()
        return instance
    
ArtworkImageFormSet = modelformset_factory(
    ArtworkImage,
    form=ArtworkImageForm,
    fields=['image', 'order'],
    extra=0,
    max_num=10,
    validate_max=True,
    can_delete=True
)

class ArtworkUploadForm(forms.ModelForm):
    class Meta:
        model = Artwork
        fields = ['title', 'description', 'price', 'original_image']

class ArtworkCensorForm(forms.Form):
    # сюда JS запишет координаты
    censored_areas = forms.CharField(widget=forms.HiddenInput)

class LessonForm(forms.ModelForm):
    class Meta:
        model = Lesson
        fields = ['title', 'description', 'price', 'start_date']
