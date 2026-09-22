def roles(request):
    user = request.user
    if not user.is_authenticated:
        return {"is_owner": False, "is_cashier": False}

    group_names = set(user.groups.values_list("name", flat=True))
    return {
        "is_owner": user.is_superuser or "Owner" in group_names,
        "is_cashier": "Cashier" in group_names,
    }