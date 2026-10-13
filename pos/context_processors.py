def roles(request):
    user = request.user
    if not user.is_authenticated:
        return {"is_owner": False, "is_cashier": False, "role_name": "No role"}

    group_names = set(user.groups.values_list("name", flat=True))
    is_owner = user.is_superuser or "Owner" in group_names
    is_cashier = "Cashier" in group_names

    if is_owner:
        role_name = "Owner"
    elif is_cashier:
        role_name = "Cashier"
    else:
        role_name = "No role"

    return {
        "is_owner": is_owner,
        "is_cashier": is_cashier,
        "role_name": role_name,
    }