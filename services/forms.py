import re
from django import forms


class SearchForm(forms.Form):
    title = forms.CharField(max_length=100)


class PaymentForm(forms.Form):
    card_number = forms.CharField(label='Card number', max_length=30)
    expiry_date = forms.CharField(label='Expiry', max_length=7)
    cvv = forms.CharField(label='CVC', max_length=4)

    def clean_card_number(self):
        raw = self.cleaned_data['card_number']
        digits = re.sub(r'\D+', '', raw)
        if len(digits) < 12 or len(digits) > 19:
            raise forms.ValidationError('Введите корректный номер карты')
        return digits

    def clean_expiry_date(self):
        value = self.cleaned_data['expiry_date'].strip()
        # очень простая проверка формата MM/YY
        if len(value) != 5 or value[2] != '/':
            raise forms.ValidationError('Формат должен быть MM/YY')
        return value

    def clean_cvv(self):
        raw = self.cleaned_data['cvv']
        digits = re.sub(r'\D+', '', raw)
        if len(digits) not in (3, 4):
            raise forms.ValidationError('CVC – 3–4 цифры')
        return digits