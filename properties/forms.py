from django import forms

from accounts.forms import BootstrapFormMixin
from accounts.models import User
from core.utils import validate_image_file

from .models import Category, Property


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.ImageField):
    def __init__(self, *args, max_files=20, **kwargs):
        self.max_files = max_files
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}))
        kwargs.setdefault("required", False)
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else [data] if data else []
        if self.required and not files:
            raise forms.ValidationError("Please add at least one photo.")
        cleaned = []
        for f in files:
            f = super().clean(f, initial)
            validate_image_file(f)
            cleaned.append(f)
        if len(cleaned) > self.max_files:
            raise forms.ValidationError(f"You can upload at most {self.max_files} images at once.")
        return cleaned


# The only kinds of property owners may submit from the website, with a short description for the chooser.
PUBLIC_TYPES = [
    ("house", "House", "fa-house", "Family homes, bungalows, villas"),
    ("apartment", "Apartment", "fa-building", "Flats and apartments in a building"),
    ("plot", "Plot", "fa-vector-square", "Building plot for a house"),
    ("land", "Land", "fa-mountain-sun", "Farm land or large parcels"),
    ("commercial", "Commercial", "fa-shop", "Shops, offices, warehouses, buildings"),
]
PUBLIC_TYPE_VALUES = [t[0] for t in PUBLIC_TYPES]
PUBLIC_PURPOSES = [("sale", "For sale", "fa-tag"), ("rent", "For rent", "fa-key")]

# Fields that don't apply to a given kind of property are removed from the form.
HIDDEN_FIELDS = {
    "plot": {"bedrooms", "bathrooms", "parking_spaces", "furnished"},
    "land": {"bedrooms", "bathrooms", "parking_spaces", "furnished"},
    "commercial": {"bedrooms", "furnished"},
}


class PublicPropertyForm(BootstrapFormMixin, forms.ModelForm):
    """Owners submit a property from the public website, without an account.

    The kind of property and sale/rent are chosen first (step 1) and passed in;
    the form then only shows the fields that make sense for that kind.
    """

    new_images = MultipleImageField(label="Property photos", required=True, max_files=15,
                                    help_text="Add 1 to 15 clear photos (JPG, PNG or WEBP, max 5 MB each).")
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"autocomplete": "off", "tabindex": "-1"}))
    agree = forms.BooleanField(label="I confirm I am the owner (or authorised to sell/rent) and the details are correct.")

    class Meta:
        model = Property
        fields = [
            "owner_name", "owner_phone", "owner_email",
            "title", "description", "property_type", "purpose", "price", "currency", "rent_period",
            "sector", "village", "address",
            "bedrooms", "bathrooms", "parking_spaces", "size_sqm", "furnished", "amenities",
        ]
        labels = {"owner_name": "Your full name", "owner_phone": "Your phone number",
                  "owner_email": "Your email (optional)", "sector": "Sector / area", "address": "Street / landmark",
                  "rent_period": "Rent is paid"}
        widgets = {"description": forms.Textarea(attrs={"rows": 4,
                   "placeholder": "Describe the property: rooms, condition, neighbourhood, documents available..."}),
                   "owner_phone": forms.TextInput(attrs={"placeholder": "07XX XXX XXX"})}

    def __init__(self, *args, property_type, purpose, **kwargs):
        if property_type not in PUBLIC_TYPE_VALUES or purpose not in ("sale", "rent"):
            raise ValueError("Unsupported property type or purpose")
        super().__init__(*args, **kwargs)
        self.property_type, self.purpose = property_type, purpose
        # Type and purpose come from step 1 and are fixed; they are not editable here.
        self.fields.pop("property_type")
        self.fields.pop("purpose")
        for name in HIDDEN_FIELDS.get(property_type, set()):
            self.fields.pop(name)
        if purpose == "sale":
            self.fields.pop("rent_period")
        else:
            self.fields["rent_period"].required = True
            self.fields["rent_period"].widget = forms.Select(
                choices=[("", "Select"), ("month", "per month"), ("year", "per year"), ("night", "per night")],
                attrs={"class": "form-select"})
            self.fields["price"].label = "Rent price"
        if property_type in Property.LAND_TYPES:
            self.fields["size_sqm"].required = True
            self.fields["size_sqm"].label = "Size (m²)"
        for name in ("owner_name", "owner_phone", "sector"):
            self.fields[name].required = True
        self.fields["sector"].widget.attrs["placeholder"] = "e.g. Kimironko"
        noun = dict((t[0], t[1].lower()) for t in PUBLIC_TYPES)[property_type]
        examples = {"house": "4-bedroom house in Kibagabaga", "apartment": "2-bedroom apartment in Kimihurura",
                    "plot": "600 m² plot in Rebero", "land": "2 hectares of farm land in Bugesera",
                    "commercial": "Shop space on KN 4 Ave"}
        self.fields["title"].widget.attrs["placeholder"] = f"e.g. {examples[property_type]}"
        self.fields["title"].label = f"Title of your {noun}"

    def save(self, commit=True):
        prop = super().save(commit=False)
        prop.property_type = self.property_type
        prop.purpose = self.purpose
        if commit:
            prop.save()
        return prop

    def clean_website(self):
        # Honeypot: humans never see this field; bots fill it in.
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("Submission rejected.")
        return ""


class SubmissionStatusForm(BootstrapFormMixin, forms.Form):
    reference = forms.CharField(max_length=12, label="Reference number",
                                widget=forms.TextInput(attrs={"placeholder": "TP-XXXXXXXX"}))
    phone = forms.CharField(max_length=20, label="Phone number used when submitting")


AMENITY_CHOICES = [(a, a) for a in [
    "Security guard", "CCTV", "Fence & gate", "Garden", "Water tank", "Backup generator", "Solar power",
    "Wi-Fi", "Elevator", "Swimming pool", "Gym", "Balcony", "Servant quarters",
    "Title deed", "Road access", "Electricity", "Water connection",
]]
RENT_PERIODS = [("", "Select"), ("month", "per month"), ("year", "per year"), ("night", "per night")]

# Category is set automatically from the type (categories are still managed in Settings).
TYPE_CATEGORY_SLUGS = {"house": "residential", "villa": "residential", "apartment": "apartments",
                       "plot": "land-plots", "land": "land-plots", "commercial": "commercial", "office": "offices"}

ROOM_TYPES = ("house", "apartment", "villa")          # need bedrooms + bathrooms
FURNISHABLE_TYPES = ("house", "apartment", "villa")


class PropertyForm(BootstrapFormMixin, forms.ModelForm):
    """Staff (admin / agent) property form: only what's needed to publish, with clear required fields."""

    property_type = forms.ChoiceField(widget=forms.RadioSelect, label="Type of property")
    purpose = forms.ChoiceField(choices=Property.Purpose.choices, widget=forms.RadioSelect, label="Sale or rent")
    rent_period = forms.ChoiceField(choices=RENT_PERIODS, required=False, label="Rent is paid")
    amenity_choices = forms.MultipleChoiceField(choices=AMENITY_CHOICES, required=False,
                                                widget=forms.CheckboxSelectMultiple, label="Amenities")
    other_amenities = forms.CharField(required=False, label="Other amenities",
                                      widget=forms.TextInput(attrs={"placeholder": "Comma separated, e.g. Rooftop terrace"}))
    new_images = MultipleImageField(label="Add photos", help_text="JPG, PNG or WEBP, max 5 MB each. The first photo becomes the cover.")

    class Meta:
        model = Property
        fields = [
            "property_type", "purpose", "title", "price", "currency", "rent_period", "description",
            "sector", "address",
            "bedrooms", "bathrooms", "parking_spaces", "size_sqm", "furnished",
            "availability", "owner_name", "owner_phone", "owner_email", "agent",
        ]
        labels = {"owner_name": "Owner name", "owner_phone": "Owner phone", "owner_email": "Owner email",
                  "sector": "Sector / area", "address": "Street / landmark", "size_sqm": "Size (m²)", "parking_spaces": "Parking spaces",
                  "furnished": "Furnished"}
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        type_choices = [(t[0], t[1]) for t in PUBLIC_TYPES]
        current = self.instance.property_type if self.instance.pk else ""
        if current and current not in PUBLIC_TYPE_VALUES:  # keep older types (office, villa) editable
            type_choices.append((current, self.instance.get_property_type_display()))
        self.fields["property_type"].choices = type_choices

        for name in ("title", "description", "price", "sector", "owner_name", "owner_phone"):
            self.fields[name].required = True
        self.fields["title"].widget.attrs["placeholder"] = "e.g. Modern 4-bedroom house in Kibagabaga"
        self.fields["sector"].widget.attrs["placeholder"] = "e.g. Kimironko"
        self.fields["owner_phone"].widget.attrs["placeholder"] = "07XX XXX XXX"
        self.fields["address"].widget.attrs["placeholder"] = "e.g. KG 11 Ave, near Kimironko market"
        # Conditional fields: required-ness is enforced in clean()
        for name in ("bedrooms", "bathrooms", "parking_spaces", "size_sqm"):
            self.fields[name].required = False
            self.fields[name].widget.attrs["min"] = 0

        if self.instance.pk:
            chosen = [a for a in self.instance.amenity_list if a in dict(AMENITY_CHOICES)]
            self.initial["amenity_choices"] = chosen
            self.initial["other_amenities"] = ", ".join(a for a in self.instance.amenity_list if a not in chosen)
        else:
            self.fields.pop("availability")  # new listings are always "available"

        self.fields["agent"].queryset = User.objects.filter(role=User.Role.AGENT, is_active=True)
        self.fields["agent"].label = "Assigned agent"
        self.fields["agent"].empty_label = "Not assigned yet"
        if user and not user.is_admin_role:
            self.fields.pop("agent")  # only admins assign agents

        # The owner already gave their name and contacts when submitting on the website,
        # so staff publishing it don't fill them in again (the saved values are kept).
        if self.is_owner_submission:
            for name in ("owner_name", "owner_phone", "owner_email"):
                self.fields.pop(name)

    @property
    def is_owner_submission(self):
        return bool(self.instance.pk) and self.instance.source == Property.Source.WEBSITE

    def clean(self):
        data = super().clean()
        ptype, purpose = data.get("property_type"), data.get("purpose")
        if ptype in ROOM_TYPES:
            for name in ("bedrooms", "bathrooms"):
                if data.get(name) in (None, ""):
                    self.add_error(name, "Required for a " + dict(Property.PropertyType.choices)[ptype].lower() + ".")
        else:
            data["bedrooms"] = 0
            if ptype in Property.LAND_TYPES:
                data["bathrooms"] = 0
                data["parking_spaces"] = 0
        if ptype in Property.LAND_TYPES and not data.get("size_sqm"):
            self.add_error("size_sqm", "Size is required for plots and land.")
        if ptype not in FURNISHABLE_TYPES:
            data["furnished"] = False
        for name in ("bedrooms", "bathrooms", "parking_spaces"):
            if data.get(name) in (None, ""):
                data[name] = 0
        if purpose == Property.Purpose.RENT and not data.get("rent_period"):
            self.add_error("rent_period", "Say how the rent is paid.")
        if purpose == Property.Purpose.SALE:
            data["rent_period"] = ""
        # At least one photo is needed before a listing can go out
        has_photos = self.instance.pk and self.instance.images.exists()
        if not has_photos and not data.get("new_images"):
            self.add_error("new_images", "Add at least one photo.")
        return data

    def save(self, commit=True):
        prop = super().save(commit=False)
        extra = [a.strip() for a in self.cleaned_data.get("other_amenities", "").split(",") if a.strip()]
        prop.amenities = ", ".join(list(self.cleaned_data.get("amenity_choices", [])) + extra)[:300]
        slug = TYPE_CATEGORY_SLUGS.get(prop.property_type)
        if slug and (not prop.category_id or prop.category.slug != slug):
            prop.category = Category.objects.filter(slug=slug).first() or prop.category
        if commit:
            prop.save()
        return prop


class PropertyReviewForm(BootstrapFormMixin, forms.ModelForm):
    """Admin approval/rejection."""

    class Meta:
        model = Property
        fields = ["approval_status", "rejection_reason", "is_featured", "agent"]


class CategoryForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "icon", "description"]
